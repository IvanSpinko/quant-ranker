# Daily pipeline: the scheduler invokes ingest; a successful ingest invokes predict.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pipeline" {
  name               = "${var.project}-pipeline"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "pipeline" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*", "${aws_cloudwatch_log_group.predict.arn}:*"]
  }
  statement {
    sid       = "ReadWriteData"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/*"]
  }
  statement {
    sid       = "ListData"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement {
    sid       = "ChainToPredict"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.predict.arn]
  }
}

resource "aws_iam_role_policy" "pipeline" {
  name   = "${var.project}-pipeline"
  role   = aws_iam_role.pipeline.id
  policy = data.aws_iam_policy_document.pipeline.json
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${var.project}-ingest"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "predict" {
  name              = "/aws/lambda/${var.project}-predict"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "ingest" {
  function_name = "${var.project}-ingest"
  role          = aws_iam_role.pipeline.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = ["x86_64"]
  memory_size   = 1024
  timeout       = 900
  image_config {
    command = ["handler.ingest_handler"]
  }
  environment {
    variables = { DATA_BUCKET = aws_s3_bucket.data.id }
  }
  depends_on = [aws_cloudwatch_log_group.ingest]
}

resource "aws_lambda_function" "predict" {
  function_name = "${var.project}-predict"
  role          = aws_iam_role.pipeline.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = ["x86_64"]
  memory_size   = 3008
  timeout       = 900
  image_config {
    command = ["handler.predict_handler"]
  }
  environment {
    variables = { DATA_BUCKET = aws_s3_bucket.data.id }
  }
  depends_on = [aws_cloudwatch_log_group.predict]
}

resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name                = aws_lambda_function.ingest.function_name
  maximum_retry_attempts       = 1
  maximum_event_age_in_seconds = 3600
  destination_config {
    on_success {
      destination = aws_lambda_function.predict.arn
    }
  }
}

resource "aws_lambda_function_event_invoke_config" "predict" {
  function_name                = aws_lambda_function.predict.function_name
  maximum_retry_attempts       = 1
  maximum_event_age_in_seconds = 3600
}

# EventBridge Scheduler invokes asynchronously, so the destination above chains predict.
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.project}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.project}-scheduler"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.ingest.arn
    }]
  })
}

resource "aws_scheduler_schedule" "daily" {
  name                         = "${var.project}-daily"
  state                        = var.schedule_enabled ? "ENABLED" : "DISABLED"
  schedule_expression          = var.schedule
  schedule_expression_timezone = var.schedule_timezone
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_lambda_function.ingest.arn
    role_arn = aws_iam_role.scheduler.arn
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}
