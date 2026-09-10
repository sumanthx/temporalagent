#!/usr/bin/env bash
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
PROFILE="${AWS_PROFILE:-default}"
PROJECT="${PROJECT_NAME:-sharepoint-temporal-agent}"
STACK="${STACK_NAME:-sharepoint-temporal-agent-core}"
ACCOUNT="$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)"
BUCKET="${ARTIFACT_BUCKET:-${PROJECT}-${ACCOUNT}-${REGION}}"

aws cloudformation deploy --template-file infra/core.yaml --stack-name "$STACK" \
  --region "$REGION" --profile "$PROFILE" --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides ProjectName="$PROJECT" ArtifactBucketName="$BUCKET" \
  --tags Project="$PROJECT"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --profile "$PROFILE" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

REPO="$(output RuntimeRepositoryUri)"
ROLE="$(output RuntimeRoleArn)"
KB_ROLE="$(output KnowledgeBaseRoleArn)"
TABLE="$(output TemporalFactsTable)"
QUEUE="$(output ChangeQueueUrl)"
BUILD_PROJECT="$(output RuntimeBuildProject)"
CLIENT_ID="$(output AgentMachineClientId)"
ISSUER="$(output AgentIssuerUrl)"
TOKEN_URL="$(output AgentTokenUrl)"
IMPORT_ROLE="$(output NeptuneImportRoleArn)"

zip -qr /tmp/sharepoint-temporal-runtime.zip Dockerfile.tools Dockerfile.agent \
  pyproject.toml README.md \
  temporal_agent fixtures -x '*/__pycache__/*' '*.pyc'
aws s3 cp /tmp/sharepoint-temporal-runtime.zip "s3://${BUCKET}/build/runtime.zip" \
  --region "$REGION" --profile "$PROFILE"
TOOLS_BUILD="$(aws codebuild start-build --project-name "$BUILD_PROJECT" \
  --environment-variables-override \
  name=DOCKERFILE,value=Dockerfile.tools,type=PLAINTEXT \
  name=IMAGE_TAG,value=tools,type=PLAINTEXT \
  --region "$REGION" --profile "$PROFILE" --query build.id --output text)"
AGENT_BUILD="$(aws codebuild start-build --project-name "$BUILD_PROJECT" \
  --environment-variables-override \
  name=DOCKERFILE,value=Dockerfile.agent,type=PLAINTEXT \
  name=IMAGE_TAG,value=orchestrator,type=PLAINTEXT \
  --region "$REGION" --profile "$PROFILE" --query build.id --output text)"

wait_build() {
  local build_id="$1"
  local status="IN_PROGRESS"
  while [[ "$status" == "IN_PROGRESS" ]]; do
    sleep 5
    status="$(aws codebuild batch-get-builds --ids "$build_id" --region "$REGION" \
      --profile "$PROFILE" --query 'builds[0].buildStatus' --output text)"
  done
  [[ "$status" == "SUCCEEDED" ]]
}
wait_build "$TOOLS_BUILD"
wait_build "$AGENT_BUILD"

python3 scripts/seed_aws.py --bucket "$BUCKET" --table "$TABLE" \
  --queue-url "$QUEUE" --region "$REGION"

VECTOR_BUCKET="${PROJECT}-${ACCOUNT}"
if ! aws s3vectors get-vector-bucket --vector-bucket-name "$VECTOR_BUCKET" \
  --region "$REGION" --profile "$PROFILE" >/dev/null 2>&1; then
  aws s3vectors create-vector-bucket --vector-bucket-name "$VECTOR_BUCKET" \
    --region "$REGION" --profile "$PROFILE"
fi
if ! aws s3vectors get-index --vector-bucket-name "$VECTOR_BUCKET" \
  --index-name current-content --region "$REGION" --profile "$PROFILE" >/dev/null 2>&1; then
  aws s3vectors create-index --vector-bucket-name "$VECTOR_BUCKET" \
    --index-name current-content --data-type float32 --dimension 1024 \
    --distance-metric cosine --region "$REGION" --profile "$PROFILE"
fi

GRAPH_ID="$(aws neptune-graph list-graphs --region "$REGION" --profile "$PROFILE" \
  --query "graphs[?name=='${PROJECT}'].id | [0]" --output text)"
if [[ "$GRAPH_ID" == "None" ]]; then
  GRAPH_ID="$(aws neptune-graph create-graph --graph-name "$PROJECT" \
    --provisioned-memory 16 --replica-count 0 \
    --region "$REGION" --profile "$PROFILE" --query id --output text)"
fi

INDEX_ARN="$(aws s3vectors get-index --vector-bucket-name "$VECTOR_BUCKET" \
  --index-name current-content --region "$REGION" --profile "$PROFILE" \
  --query index.indexArn --output text)"
VECTOR_ARN="$(aws s3vectors get-vector-bucket --vector-bucket-name "$VECTOR_BUCKET" \
  --region "$REGION" --profile "$PROFILE" \
  --query vectorBucket.vectorBucketArn --output text)"

KB_ID="$(aws bedrock-agent list-knowledge-bases --region "$REGION" --profile "$PROFILE" \
  --query "knowledgeBaseSummaries[?name=='${PROJECT}'].knowledgeBaseId | [0]" --output text)"
if [[ "$KB_ID" == "None" ]]; then
  KB_ID="$(aws bedrock-agent create-knowledge-base --name "$PROJECT" \
    --role-arn "$KB_ROLE" \
    --knowledge-base-configuration "{\"type\":\"VECTOR\",\"vectorKnowledgeBaseConfiguration\":{\"embeddingModelArn\":\"arn:aws:bedrock:${REGION}::foundation-model/amazon.titan-embed-text-v2:0\",\"embeddingModelConfiguration\":{\"bedrockEmbeddingModelConfiguration\":{\"dimensions\":1024,\"embeddingDataType\":\"FLOAT32\"}}}}" \
    --storage-configuration "{\"type\":\"S3_VECTORS\",\"s3VectorsConfiguration\":{\"vectorBucketArn\":\"${VECTOR_ARN}\",\"indexArn\":\"${INDEX_ARN}\"}}" \
    --region "$REGION" --profile "$PROFILE" \
    --query knowledgeBase.knowledgeBaseId --output text)"
fi

TOOLS_RUNTIME_ID="$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
  --profile "$PROFILE" \
  --query "agentRuntimes[?agentRuntimeName=='sharepoint_temporal_tools'].agentRuntimeId | [0]" \
  --output text)"
if [[ "$TOOLS_RUNTIME_ID" == "None" ]]; then
  TOOLS_RUNTIME_ID="$(aws bedrock-agentcore-control create-agent-runtime \
    --agent-runtime-name sharepoint_temporal_tools \
    --description "SharePoint temporal structured MCP tools" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${REPO}:tools\"}}" \
    --role-arn "$ROLE" --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"MCP"}' \
    --authorizer-configuration "{\"customJWTAuthorizer\":{\"discoveryUrl\":\"${ISSUER}/.well-known/openid-configuration\",\"allowedClients\":[\"${CLIENT_ID}\"]}}" \
    --environment-variables "{\"AWS_REGION\":\"${REGION}\",\"RUNTIME_KIND\":\"tools\",\"TEMPORAL_FACTS_TABLE\":\"${TABLE}\",\"ARTIFACT_BUCKET\":\"${BUCKET}\",\"KNOWLEDGE_BASE_ID\":\"${KB_ID}\",\"NEPTUNE_GRAPH_ID\":\"${GRAPH_ID}\"}" \
    --region "$REGION" --profile "$PROFILE" --query agentRuntimeId --output text)"
fi
TOOLS_RUNTIME_ARN="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT}:runtime/${TOOLS_RUNTIME_ID}"

ORCHESTRATOR_RUNTIME_ID="$(aws bedrock-agentcore-control list-agent-runtimes \
  --region "$REGION" --profile "$PROFILE" \
  --query "agentRuntimes[?agentRuntimeName=='sharepoint_temporal_orchestrator'].agentRuntimeId | [0]" \
  --output text)"
if [[ "$ORCHESTRATOR_RUNTIME_ID" == "None" ]]; then
  ORCHESTRATOR_RUNTIME_ID="$(aws bedrock-agentcore-control create-agent-runtime \
    --agent-runtime-name sharepoint_temporal_orchestrator \
    --description "Natural-language Bedrock orchestrator over temporal MCP tools" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${REPO}:orchestrator\"}}" \
    --role-arn "$ROLE" --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --authorizer-configuration "{\"customJWTAuthorizer\":{\"discoveryUrl\":\"${ISSUER}/.well-known/openid-configuration\",\"allowedClients\":[\"${CLIENT_ID}\"]}}" \
    --environment-variables "{\"AWS_REGION\":\"${REGION}\",\"RUNTIME_KIND\":\"orchestrator\",\"BEDROCK_AGENT_MODEL_ID\":\"amazon.nova-lite-v1:0\",\"MCP_RUNTIME_ARN\":\"${TOOLS_RUNTIME_ARN}\",\"MCP_USER_POOL_ID\":\"$(output AgentUserPoolId)\",\"MCP_CLIENT_ID\":\"${CLIENT_ID}\",\"MCP_TOKEN_URL\":\"${TOKEN_URL}\"}" \
    --region "$REGION" --profile "$PROFILE" --query agentRuntimeId --output text)"
fi

cat <<EOF
Stack: $STACK
Artifact bucket: $BUCKET
Temporal table: $TABLE
Queue: $QUEUE
Neptune graph: $GRAPH_ID
S3 Vector bucket/index: $VECTOR_BUCKET/current-content
Knowledge base: $KB_ID
AgentCore tools runtime: $TOOLS_RUNTIME_ID
AgentCore orchestrator runtime: $ORCHESTRATOR_RUNTIME_ID
EOF
