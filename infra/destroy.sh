#!/usr/bin/env bash
set -euo pipefail
echo "This project retains S3, DynamoDB, and ECR data on stack deletion."
echo "Delete managed AgentCore, Knowledge Base, S3 Vectors, and Neptune resources explicitly."
echo "Review resources tagged Project=sharepoint-temporal-agent before cleanup."
