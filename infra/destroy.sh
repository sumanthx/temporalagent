#!/usr/bin/env bash
set -euo pipefail
echo "This project retains S3 and DynamoDB data on stack deletion."
echo "Delete the AgentCore runtimes, Gateway, Gateway target, and Lambda functions explicitly."
echo "Delete the source-sync EventBridge rule explicitly."
echo "If ENABLE_NEPTUNE=true was used, delete the optional Neptune graph explicitly."
echo "Review resources tagged Project=sharepoint-temporal-agent before cleanup."
