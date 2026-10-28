output "bucket" {
  value = aws_s3_bucket.data.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.lambda.repository_url
}

output "data_url" {
  description = "Base URL the desktop app downloads published files from"
  value       = "https://${aws_cloudfront_distribution.published.domain_name}"
}

output "ingest_function" {
  value = aws_lambda_function.ingest.function_name
}

output "predict_function" {
  value = aws_lambda_function.predict.function_name
}
