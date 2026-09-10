# Deployment inventory

Physical AWS identifiers are intentionally excluded from source control.

Stable logical names:

- CloudFormation stack: `sharepoint-temporal-agent-core`
- Project tag: `sharepoint-temporal-agent`
- HTTP runtime: `sharepoint_temporal_orchestrator`
- MCP runtime: `sharepoint_temporal_tools`
- Ingestion function: `sharepoint-temporal-agent-ingestion`
- Optional Neptune graph: `sharepoint-temporal-agent`

The stack owns S3, SQS/DLQ, DynamoDB, IAM and Cognito resources. The deployment
script creates or updates the Lambda worker and two AgentCore CodeZip runtimes.

## Discover identifiers

```bash
AWS_PROFILE=<profile> AWS_REGION=<region> \
aws cloudformation describe-stacks \
  --stack-name sharepoint-temporal-agent-core \
  --query 'Stacks[0].Outputs'
```

```bash
AWS_PROFILE=<profile> AWS_REGION=<region> \
aws bedrock-agentcore-control list-agent-runtimes \
  --query 'agentRuntimes[?contains(agentRuntimeName, `sharepoint_temporal`)]'
```

## Invoke

```bash
python3 scripts/ask_aws.py \
  --profile <profile> \
  --region <region> \
  "Who owned Atlas on 2024-02-01?"
```

The client secret is read through the Cognito API and held only in memory while
obtaining a short-lived client-credentials token.

## Security assumptions

- Both AgentCore runtimes require Cognito JWTs.
- The orchestrator calls tools only through the MCP runtime.
- Historical evidence is filtered using the mock source's current ACL.
- The forwarded demo principal is not production user authentication.
- Production must derive user and group IDs from a verified Entra ID token.
- S3 public access is blocked and DynamoDB point-in-time recovery is enabled.

## Optional Neptune

Neptune is disabled by default:

```bash
ENABLE_NEPTUNE=true AWS_PROFILE=<profile> AWS_REGION=<region> \
  bash infra/deploy.sh
```

The current app does not query it. Enabling it only provisions a graph for the
future bounded multi-hop phase.

## Cleanup

S3 and DynamoDB use retention policies. The AgentCore runtimes, Lambda function,
and optional Neptune graph are managed outside the stack and require explicit
cleanup. Previously deployed ECR, CodeBuild, Knowledge Base or S3 Vector
resources are no longer used and may remain until explicitly removed.
