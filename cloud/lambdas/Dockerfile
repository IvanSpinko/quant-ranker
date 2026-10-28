# One image for every pipeline function; Terraform picks the handler per function.
# Build from the repository root:
#   docker build --platform linux/amd64 --provenance=false -f cloud/lambdas/Dockerfile .
FROM public.ecr.aws/lambda/python:3.13

# LightGBM needs the OpenMP runtime; HOME and caches must point at writable /tmp.
RUN dnf install -y libgomp && dnf clean all
ENV HOME=/tmp XDG_CACHE_HOME=/tmp/.cache PYTHONUNBUFFERED=1

COPY cloud/lambdas/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY core/quant_core ${LAMBDA_TASK_ROOT}/quant_core
COPY cloud/lambdas/handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.ingest_handler"]
