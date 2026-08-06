#!/usr/bin/env bash
# Run on the MAC. Creates the EC2 instance JobFinder will live on.
# Requires: awscli v2 installed and `aws configure` already done.
#
#   bash deploy/provision-aws.sh
#
# CREATES BILLABLE RESOURCES (~$27/mo: t4g.medium + 30GB gp3). Prompts first.
set -euo pipefail

REGION="${REGION:-us-east-1}"
NAME="${NAME:-jobfinder}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t4g.medium}"   # 2 vCPU / 4 GB arm64; Camoufox needs ~1.3 GB
VOLUME_GB="${VOLUME_GB:-30}"
KEY_PATH="$HOME/.ssh/${NAME}.pem"

aws sts get-caller-identity --region "$REGION" >/dev/null || {
  echo "aws credentials not working — run 'aws configure' first"; exit 1; }

# Canonical's SSM alias always points at the current Ubuntu 24.04 arm64 image.
AMI=$(aws ssm get-parameters --region "$REGION" \
  --names /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --query 'Parameters[0].Value' --output text)

MY_IP=$(curl -fsS https://checkip.amazonaws.com | tr -d '\n')

echo "region=$REGION  type=$INSTANCE_TYPE  ami=$AMI  disk=${VOLUME_GB}GB"
echo "ssh will be locked to $MY_IP/32"
read -rp "create these resources? [y/N] " ok
[ "$ok" = "y" ] || { echo "aborted"; exit 0; }

# --- key pair -----------------------------------------------------------
if [ ! -f "$KEY_PATH" ]; then
  aws ec2 create-key-pair --region "$REGION" --key-name "$NAME" \
    --key-type ed25519 --query KeyMaterial --output text > "$KEY_PATH"
  chmod 400 "$KEY_PATH"
  echo "wrote $KEY_PATH"
fi

# --- security group: outbound only + SSH from this machine --------------
SG=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=$NAME" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)
if [ "$SG" = "None" ] || [ -z "$SG" ]; then
  SG=$(aws ec2 create-security-group --region "$REGION" --group-name "$NAME" \
    --description "JobFinder: SSH from operator only; Telegram is outbound long-poll" \
    --query GroupId --output text)
fi
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG" \
  --protocol tcp --port 22 --cidr "$MY_IP/32" 2>/dev/null || true

# --- instance -----------------------------------------------------------
IID=$(aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" --instance-type "$INSTANCE_TYPE" \
  --key-name "$NAME" --security-group-ids "$SG" \
  --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$VOLUME_GB,VolumeType=gp3,DeleteOnTermination=true}" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
  --metadata-options "HttpTokens=required" \
  --query 'Instances[0].InstanceId' --output text)
echo "launched $IID — waiting for running state"
aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"

# --- elastic IP (free while attached; gives the proxy a stable IP to allow) --
ALLOC=$(aws ec2 allocate-address --region "$REGION" --domain vpc \
  --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME}]" \
  --query AllocationId --output text)
aws ec2 associate-address --region "$REGION" \
  --instance-id "$IID" --allocation-id "$ALLOC" >/dev/null
IP=$(aws ec2 describe-addresses --region "$REGION" --allocation-ids "$ALLOC" \
  --query 'Addresses[0].PublicIp' --output text)

cat <<EOF

instance : $IID
ip       : $IP
key      : $KEY_PATH

next:
  ssh -i $KEY_PATH ubuntu@$IP
  git clone https://github.com/rakhmanovadam/JobFinder.git ~/JobFinder
  bash ~/JobFinder/deploy/bootstrap.sh

then copy the two gitignored files over (ROTATE THE KEYS FIRST):
  scp -i $KEY_PATH .env ubuntu@$IP:~/JobFinder/.env
  scp -i $KEY_PATH tailor/resume_data.json ubuntu@$IP:~/JobFinder/tailor/

teardown:
  aws ec2 terminate-instances --region $REGION --instance-ids $IID
  aws ec2 release-address --region $REGION --allocation-id $ALLOC
EOF
