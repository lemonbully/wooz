#!/bin/bash
# OneWooz custom-domain edge: boot script for the standalone Caddy servers
# (launch template "onewooz-edge", groups onewooz-edge-a / onewooz-edge-b).
#
# Each server runs the same onewooz-caddy image the old ECS "edge" service ran,
# with host networking on :80/:443 (TCP) and :443 (UDP, HTTP/3). Certificates
# live in ElastiCache Redis, so every edge serves every verified domain and no
# certificate is ever issued twice. If Caddy exits, Docker restarts it; if the
# server dies, its one-server Auto Scaling group replaces it.
set -euo pipefail

REGION=ap-south-1
ACCOUNT=521831014282
REPO="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
IMAGE="$REPO/onewooz-caddy:latest"

# QUIC (HTTP/3) wants larger UDP buffers than the kernel default.
cat >/etc/sysctl.d/90-caddy-quic.conf <<'EOF'
net.core.rmem_max=7500000
net.core.wmem_max=7500000
EOF
sysctl --system >/dev/null

dnf install -y docker
systemctl enable --now docker

TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
IID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REPO"
ASK_SECRET=$(aws ssm get-parameter --region "$REGION" --name /onewooz/prod/CADDY_ASK_SECRET \
  --with-decryption --query Parameter.Value --output text)

docker pull "$IMAGE"
docker rm -f caddy 2>/dev/null || true
docker run -d --name caddy --restart always --network host \
  -e REDIS_HOST=onewooz-redis.anbsbd.0001.aps1.cache.amazonaws.com \
  -e REDIS_PORT=6379 \
  -e REDIS_TLS=false \
  -e APP_UPSTREAM=onewooz-alb-1587607702.ap-south-1.elb.amazonaws.com:8080 \
  -e ACME_EMAIL=admin@onewooz.com \
  -e CADDY_ASK_SECRET="$ASK_SECRET" \
  -v caddy-data:/data -v caddy-config:/config \
  --log-driver awslogs \
  --log-opt awslogs-region="$REGION" \
  --log-opt awslogs-group=onewooz-ecs \
  --log-opt awslogs-stream="edge/$IID" \
  "$IMAGE"

# Claim this edge's fixed IP, but only once Caddy answers locally, so a fresh
# server never takes traffic before it can serve it. The server's role (a or b)
# comes from its Name tag (onewooz-edge-a / onewooz-edge-b); the Elastic IP
# allocation for that role is in SSM. "none" means no IP is assigned to the role
# yet (for example before the cutover from the old ECS edge).
for _ in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1/ || true)
  case "$code" in 2*|3*) break ;; esac
  sleep 3
done

NAME=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/tags/instance/Name || true)
ROLE="${NAME##*-}"
ALLOC=$(aws ssm get-parameter --region "$REGION" --name "/onewooz/prod/edge/$ROLE/eip-alloc" \
  --query Parameter.Value --output text 2>/dev/null || true)
if [ -n "$ALLOC" ] && [ "$ALLOC" != "none" ]; then
  MAC=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/mac)
  ENI=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" "http://169.254.169.254/latest/meta-data/network/interfaces/macs/$MAC/interface-id")
  PRIV=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4)
  aws ec2 associate-address --region "$REGION" --allocation-id "$ALLOC" \
    --network-interface-id "$ENI" --private-ip-address "$PRIV" --allow-reassociation
fi
