#!/usr/bin/env bash
set -euo pipefail
echo "This project retains S3 and DynamoDB data on stack deletion."
echo "Delete the AgentCore runtimes and Lambda ingestion function explicitly."
echo "If ENABLE_NEPTUNE=true was used, delete the optional Neptune graph explicitly."
echo "Review resources tagged Project=sharepoint-temporal-agent before cleanup."
