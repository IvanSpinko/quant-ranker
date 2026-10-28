#!/usr/bin/env bash
# Build and push the Lambda image, then apply Terraform. Run from anywhere:
#   cloud/deploy.sh             deploy (Terraform asks before changing anything)
#   cloud/deploy.sh --backfill  also upload the history and research results afterwards
#   cloud/deploy.sh --yes       do not ask (for a reviewed plan)
# Needs Docker running, the AWS CLI profile in AWS_PROFILE_NAME (default: main) and uv.
set -euo pipefail

cd "$(dirname "$0")/.."
PROFILE="${AWS_PROFILE_NAME:-main}"
BACKFILL=false
APPROVE=()
for arg in "$@"; do
  case "$arg" in
    --backfill) BACKFILL=true ;;
    --yes) APPROVE=(-auto-approve) ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

tf() { terraform -chdir=cloud/terraform "$@"; }
TFVARS=(-var "aws_profile=$PROFILE")

tf init -input=false >/dev/null

echo "== 1/4 image repository"
tf apply -input=false "${APPROVE[@]}" "${TFVARS[@]}" -var "image_uri=pending" \
  -target=aws_ecr_repository.lambda -target=aws_ecr_lifecycle_policy.lambda
REPO="$(tf output -raw ecr_repository_url 2>/dev/null || true)"
if [[ -z "$REPO" ]]; then
  REPO="$(aws ecr describe-repositories --profile "$PROFILE" --repository-names quant-ranker \
    --query 'repositories[0].repositoryUri' --output text)"
fi
REGISTRY="${REPO%%/*}"
REGION="$(echo "$REGISTRY" | cut -d. -f4)"

echo "== 2/4 build and push $REPO"
TAG="$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- core cloud/lambdas || echo -dirty)"
docker build --platform linux/amd64 --provenance=false -f cloud/lambdas/Dockerfile -t "$REPO:$TAG" .
aws ecr get-login-password --profile "$PROFILE" --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker push "$REPO:$TAG"
DIGEST="$(aws ecr describe-images --profile "$PROFILE" --region "$REGION" --repository-name quant-ranker \
  --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text)"

echo "== 3/4 apply everything with $REPO@$DIGEST"
# Remembered so later plain applies (pause, resume, destroy) need no arguments.
printf 'image_uri = "%s"\naws_profile = "%s"\n' "$REPO@$DIGEST" "$PROFILE" > cloud/terraform/image.auto.tfvars
tf apply -input=false "${APPROVE[@]}"

if $BACKFILL; then
  echo "== 4/4 backfill"
  uv run python cloud/backfill.py --profile "$PROFILE" --bucket "$(tf output -raw bucket)"
else
  echo "== 4/4 backfill skipped (pass --backfill on the first deploy)"
fi

echo "Data URL for the app: $(tf output -raw data_url)"
