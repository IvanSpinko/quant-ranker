"""AWS Lambda entry points. The logic lives in quant_core.pipeline; these only wire up S3.

ingest_handler runs on the daily schedule; on success Lambda invokes predict_handler
(an asynchronous-invocation destination), so predict always sees fresh prices.

The numeric stack is imported inside the handlers: importing it at module level takes longer
than Lambda's 10-second init limit on a cold start, which makes Lambda run init twice.
"""

import logging
import os

logging.getLogger().setLevel(logging.INFO)


def _storage():
    from quant_core.storage import S3Storage

    return S3Storage(os.environ["DATA_BUCKET"])


def ingest_handler(event, context):
    import yfinance

    from quant_core.pipeline import ingest

    # Lambda's file system is read-only except /tmp, where yfinance keeps its time-zone cache.
    # Create the folder first: yfinance's download threads race to create it otherwise.
    os.makedirs("/tmp/yfinance", exist_ok=True)
    yfinance.set_tz_cache_location("/tmp/yfinance")
    return ingest(_storage())


def predict_handler(event, context):
    from quant_core.pipeline import predict

    return predict(_storage())
