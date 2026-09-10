# Deployment inventory

Physical AWS account IDs, ARNs, runtime IDs, bucket names, user-pool IDs, and
client IDs are deliberately not committed. Discover them from the deployed
stack and control planes when needed.

Stable logical names:

- CloudFormation stack: `sharepoint-temporal-agent-core`
- Project tag: `sharepoint-temporal-agent`
- AgentCore HTTP runtime name: `sharepoint_temporal_orchestrator`
- AgentCore MCP runtime name: `sharepoint_temporal_tools`
- Knowledge Base name: `sharepoint-temporal-agent`
- Neptune graph name: `sharepoint-temporal-agent`
- Gateway name: `sharepoint-temporal-gateway`
- Lambda function prefix: `sharepoint-temporal-agent-`

The stack also creates the evidence bucket, DynamoDB temporal and source-state
tables, SQS queue and DLQ, ECR repository, CodeBuild project, IAM roles, and
Cognito user pool/client.

## Discover physical identifiers

```bash
AWS_PROFILE=default AWS_REGION=<region> \
aws cloudformation describe-stacks \
  --stack-name sharepoint-temporal-agent-core \
  --query 'Stacks[0].Outputs'
```

```bash
AWS_PROFILE=default AWS_REGION=<region> \
aws bedrock-agentcore-control list-agent-runtimes \
  --query 'agentRuntimes[?contains(agentRuntimeName, `sharepoint_temporal`)]'
```

The natural-language demo performs this discovery automatically:

```bash
python3 scripts/ask_aws.py \
  --region <region> \
  "Who owned Atlas on 2024-02-01?"
```

The client secret is intentionally not stored in this repository. The helper
reads it through the AWS API and only keeps it in memory while obtaining a
short-lived token.

## Security behavior

- Both runtimes and Gateway require Cognito JWTs.
- The orchestrator calls tools only through the separate MCP runtime.
- The MCP runtime filters historical evidence by the mock source's current ACL.
- Gateway historical queries fail closed unless a verified caller subject is
  propagated to the Lambda request context.
- Neptune has no public connectivity.
- S3 public access is blocked; S3 and SQS use server-side encryption.
- DynamoDB point-in-time recovery is enabled.

## Costs and cleanup

AgentCore, Neptune Analytics, Bedrock embedding/Knowledge Base ingestion,
CodeBuild, S3 Vectors, Lambda, DynamoDB, SQS, S3, ECR, Cognito, and CloudWatch
may incur charges. Neptune Analytics is provisioned at 16 m-NCU and has deletion
protection enabled. Data-bearing resources use retention policies. Review
`infra/destroy.sh` and delete managed resources explicitly when the demo is no
longer needed.
