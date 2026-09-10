#!/usr/bin/env bash
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PROFILE="${AWS_PROFILE:-default}"
PROJECT="${PROJECT_NAME:-sharepoint-temporal-agent}"
STACK="${STACK_NAME:-sharepoint-temporal-agent-core}"
ENABLE_NEPTUNE="${ENABLE_NEPTUNE:-false}"
if [[ "$ENABLE_NEPTUNE" != "true" && "$ENABLE_NEPTUNE" != "false" ]]; then
  echo "ENABLE_NEPTUNE must be true or false" >&2
  exit 2
fi

ACCOUNT="$(aws sts get-caller-identity \
  --profile "$PROFILE" --query Account --output text)"
BUCKET="${ARTIFACT_BUCKET:-${PROJECT}-${ACCOUNT}-${REGION}}"

aws cloudformation deploy \
  --template-file infra/core.yaml \
  --stack-name "$STACK" \
  --region "$REGION" \
  --profile "$PROFILE" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    ProjectName="$PROJECT" \
    ArtifactBucketName="$BUCKET" \
  --tags Project="$PROJECT"

output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK" \
    --region "$REGION" \
    --profile "$PROFILE" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text
}

ROLE="$(output RuntimeRoleArn)"
INGESTION_ROLE="$(output IngestionRoleArn)"
SOURCE_CONNECTOR_ROLE="$(output SourceConnectorRoleArn)"
SOURCE_SYNC_ROLE="$(output SourceSyncRoleArn)"
GATEWAY_ROLE="$(output GatewayRoleArn)"
TABLE="$(output TemporalFactsTable)"
STATE_TABLE="$(output SourceStateTable)"
QUEUE="$(output ChangeQueueUrl)"
QUEUE_ARN="$(output ChangeQueueArn)"
USER_POOL_ID="$(output AgentUserPoolId)"
CLIENT_ID="$(output AgentMachineClientId)"
ISSUER="$(output AgentIssuerUrl)"
TOKEN_URL="$(output AgentTokenUrl)"

PACKAGE="/tmp/sharepoint-temporal-runtime.zip"
python3 scripts/package_runtime.py --output "$PACKAGE"
PACKAGE_VERSION="$(aws s3api put-object \
  --bucket "$BUCKET" \
  --key runtime/runtime.zip \
  --body "$PACKAGE" \
  --region "$REGION" \
  --profile "$PROFILE" \
  --query VersionId \
  --output text)"
VERSION_ARGS=()
if [[ "$PACKAGE_VERSION" != "None" && "$PACKAGE_VERSION" != "null" ]]; then
  VERSION_ARGS=(--version-id "$PACKAGE_VERSION")
fi

SOURCE_CONNECTOR="$(python3 scripts/deploy_source_connector.py \
  --profile "$PROFILE" \
  --region "$REGION" \
  --function-name "${PROJECT}-mock-source" \
  --role-arn "$SOURCE_CONNECTOR_ROLE" \
  --bucket "$BUCKET" \
  --key runtime/runtime.zip \
  "${VERSION_ARGS[@]}" \
  --artifact-bucket "$BUCKET")"
SOURCE_CONNECTOR_ARN="$(python3 -c \
  'import json,sys; print(json.loads(sys.argv[1])["function_arn"])' \
  "$SOURCE_CONNECTOR")"

GATEWAY="$(python3 scripts/deploy_gateway.py \
  --profile "$PROFILE" \
  --region "$REGION" \
  --gateway-name "${PROJECT}-source" \
  --role-arn "$GATEWAY_ROLE" \
  --function-arn "$SOURCE_CONNECTOR_ARN" \
  --issuer "$ISSUER" \
  --client-id "$CLIENT_ID")"
GATEWAY_URL="$(python3 -c \
  'import json,sys; print(json.loads(sys.argv[1])["gateway_url"])' \
  "$GATEWAY")"

python3 scripts/deploy_ingestion.py \
  --profile "$PROFILE" \
  --region "$REGION" \
  --function-name "${PROJECT}-ingestion" \
  --role-arn "$INGESTION_ROLE" \
  --bucket "$BUCKET" \
  --key runtime/runtime.zip \
  "${VERSION_ARGS[@]}" \
  --queue-arn "$QUEUE_ARN" \
  --facts-table "$TABLE" \
  --state-table "$STATE_TABLE"

python3 scripts/seed_aws.py \
  --profile "$PROFILE" \
  --bucket "$BUCKET" \
  --queue-url "$QUEUE" \
  --region "$REGION" \
  --skip-queue

python3 scripts/deploy_source_sync.py \
  --profile "$PROFILE" \
  --region "$REGION" \
  --function-name "${PROJECT}-source-sync" \
  --role-arn "$SOURCE_SYNC_ROLE" \
  --bucket "$BUCKET" \
  --key runtime/runtime.zip \
  "${VERSION_ARGS[@]}" \
  --artifact-bucket "$BUCKET" \
  --queue-url "$QUEUE" \
  --state-table "$STATE_TABLE" \
  --gateway-url "$GATEWAY_URL" \
  --user-pool-id "$USER_POOL_ID" \
  --client-id "$CLIENT_ID" \
  --token-url "$TOKEN_URL"

EXPECTED_EVENTS="$(grep -cve '^[[:space:]]*$' fixtures/changes.jsonl)"
python3 scripts/wait_ingestion.py \
  --profile "$PROFILE" \
  --region "$REGION" \
  --state-table "$STATE_TABLE" \
  --expected-events "$EXPECTED_EVENTS"

GRAPH_ID=""
if [[ "$ENABLE_NEPTUNE" == "true" ]]; then
  GRAPH_ID="$(aws neptune-graph list-graphs \
    --region "$REGION" \
    --profile "$PROFILE" \
    --query "graphs[?name=='${PROJECT}'].id | [0]" \
    --output text)"
  if [[ "$GRAPH_ID" == "None" ]]; then
    GRAPH_ID="$(aws neptune-graph create-graph \
      --graph-name "$PROJECT" \
      --provisioned-memory 16 \
      --replica-count 0 \
      --region "$REGION" \
      --profile "$PROFILE" \
      --query id \
      --output text)"
  fi
fi

RUNTIMES="$(python3 scripts/deploy_runtimes.py \
  --profile "$PROFILE" \
  --region "$REGION" \
  --account "$ACCOUNT" \
  --role-arn "$ROLE" \
  --bucket "$BUCKET" \
  --key runtime/runtime.zip \
  "${VERSION_ARGS[@]}" \
  --issuer "$ISSUER" \
  --client-id "$CLIENT_ID" \
  --user-pool-id "$USER_POOL_ID" \
  --token-url "$TOKEN_URL" \
  --table "$TABLE" \
  --source-gateway-url "$GATEWAY_URL")"

cat <<EOF
Stack: $STACK
Artifact bucket: $BUCKET
Temporal table: $TABLE
Change queue: $QUEUE
Ingestion function: ${PROJECT}-ingestion
Source connector function: ${PROJECT}-mock-source
Source sync function: ${PROJECT}-source-sync
AgentCore Gateway: ${PROJECT}-source
Neptune graph: ${GRAPH_ID:-disabled}
AgentCore runtimes: $RUNTIMES
EOF
