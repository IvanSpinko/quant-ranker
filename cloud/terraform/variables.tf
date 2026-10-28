variable "aws_profile" {
  description = "AWS CLI profile used for deployment"
  type        = string
  default     = "main"
}

variable "region" {
  description = "AWS region for the bucket and the functions"
  type        = string
  default     = "eu-west-1"
}

variable "project" {
  description = "Name prefix for every resource"
  type        = string
  default     = "quant-ranker"
}

variable "image_uri" {
  description = "Lambda container image, repository@sha256:digest; cloud/deploy.sh writes it to image.auto.tfvars"
  type        = string
}

variable "schedule_enabled" {
  description = "false pauses the daily pipeline without removing anything"
  type        = bool
  default     = true
}

variable "schedule" {
  description = "When the daily pipeline starts, as an EventBridge Scheduler cron in schedule_timezone"
  type        = string
  # 18:30 New York time, after the 16:00 close and once daily bars have settled.
  default = "cron(30 18 ? * MON-FRI *)"
}

variable "schedule_timezone" {
  type    = string
  default = "America/New_York"
}

variable "log_retention_days" {
  type    = number
  default = 30
}
