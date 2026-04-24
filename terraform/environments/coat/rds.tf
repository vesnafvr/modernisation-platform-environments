resource "aws_db_subnet_group" "test_db_subnet_group" {
  name       = "test-db-subnet-group"
  subnet_ids = [
    data.aws_subnet.private_subnets_a.id,
    data.aws_subnet.private_subnets_b.id
  ]
}

resource "aws_security_group" "test_db_sg" {
  name        = "${local.application_name}-${local.environment}-test-db-security-group"
  description = "Test DB Security Group"
  vpc_id      = data.aws_vpc.shared.id

  egress {
    description = "outbound access"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_rds_cluster" "test_db_cluster" {
  cluster_identifier      = "test-db-cluster"
  engine                  = "aurora-mysql"
  # Aurora Limitless (DB shard groups) requires a newer Aurora MySQL 3.x engine.
  engine_version          = "8.0.mysql_aurora.3.13.2"
  availability_zones      = ["eu-west-2a", "eu-west-2b", "eu-west-2c"]
  database_name           = "mydb"
  master_username         = "foo"
  master_password         = "must_be_eight_characters"
  backup_retention_period = 5
  preferred_backup_window = "07:00-09:00"
  skip_final_snapshot = true

  storage_encrypted   = true

  db_subnet_group_name = aws_db_subnet_group.test_db_subnet_group.name
  vpc_security_group_ids = [aws_security_group.test_db_sg.id]
}

resource "aws_rds_global_cluster" "test_global_cluster" {
  global_cluster_identifier = "global-test"
  engine                    = "aurora-mysql"
  database_name             = "example_db"
}

resource "aws_db_cluster_snapshot" "example" {
  db_cluster_identifier          = aws_rds_cluster.test_db_cluster.id
  db_cluster_snapshot_identifier = "resourcetestsnapshot1234"
}

resource "aws_rds_cluster_endpoint" "eligible" {
  cluster_identifier          = aws_rds_cluster.test_db_cluster.id
  cluster_endpoint_identifier = "reader"
  custom_endpoint_type        = "READER"
}

resource "aws_rds_cluster_parameter_group" "default" {
  name        = "rds-cluster-pg"
  family      = "aurora5.6"
  description = "RDS default cluster parameter group"

  parameter {
    name  = "character_set_server"
    value = "utf8"
  }

  parameter {
    name  = "character_set_client"
    value = "utf8"
  }
}

resource "aws_db_instance" "default" {
  allocated_storage    = 10
  db_name              = "mydb"
  engine               = "mysql"
  engine_version       = "8.0"
  instance_class       = "db.t3.micro"
  username             = "foo"
  password             = "foobarbaz"
  parameter_group_name = aws_db_parameter_group.test_db_parameter_group.name
  backup_retention_period = 1
  skip_final_snapshot  = true

  publicly_accessible = false
  storage_encrypted   = true

  db_subnet_group_name = aws_db_subnet_group.test_db_subnet_group.name
  vpc_security_group_ids = [aws_security_group.test_db_sg.id]
}

# Validates SCP allows rds:CreateDBProxy by creating a minimal proxy.
resource "aws_secretsmanager_secret" "test_db_proxy_secret" {
  name = "${local.application_name}-${local.environment}-test-db-proxy-secret"
}

resource "aws_secretsmanager_secret_version" "test_db_proxy_secret_version" {
  secret_id = aws_secretsmanager_secret.test_db_proxy_secret.id
  secret_string = jsonencode({
    username = aws_rds_cluster.test_db_cluster.master_username
    password = aws_rds_cluster.test_db_cluster.master_password
  })
}

data "aws_iam_policy_document" "test_db_proxy_assume_role" {
  statement {
    effect = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["rds.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "test_db_proxy_secret_access" {
  statement {
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue"
    ]
    resources = [aws_secretsmanager_secret.test_db_proxy_secret.arn]
  }
}

resource "aws_iam_role" "test_db_proxy_role" {
  name               = "${local.application_name}-${local.environment}-test-db-proxy-role"
  assume_role_policy = data.aws_iam_policy_document.test_db_proxy_assume_role.json
}

resource "aws_iam_role_policy" "test_db_proxy_secret_access" {
  name   = "${local.application_name}-${local.environment}-test-db-proxy-secret-access"
  role   = aws_iam_role.test_db_proxy_role.id
  policy = data.aws_iam_policy_document.test_db_proxy_secret_access.json
}

resource "aws_db_proxy" "test_proxy" {
  name                   = "${local.application_name}-${local.environment}-test-proxy"
  engine_family          = "MYSQL"
  role_arn               = aws_iam_role.test_db_proxy_role.arn
  vpc_security_group_ids = [aws_security_group.test_db_sg.id]
  vpc_subnet_ids = [
    data.aws_subnet.private_subnets_a.id,
    data.aws_subnet.private_subnets_b.id
  ]
  require_tls = false

  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "DISABLED"
    secret_arn  = aws_secretsmanager_secret.test_db_proxy_secret.arn
  }

  depends_on = [aws_secretsmanager_secret_version.test_db_proxy_secret_version]
}

# Validates SCP allows rds:CreateDBProxyEndpoint by creating an endpoint.
resource "aws_db_proxy_endpoint" "test_proxy_endpoint" {
  db_proxy_name          = aws_db_proxy.test_proxy.name
  db_proxy_endpoint_name = "${local.application_name}-${local.environment}-test-proxy-endpoint"
  vpc_security_group_ids = [aws_security_group.test_db_sg.id]
  vpc_subnet_ids = [
    data.aws_subnet.private_subnets_a.id,
    data.aws_subnet.private_subnets_b.id
  ]
  target_role = "READ_WRITE"
}

# Validates SCP allows rds:CreateDBParameterGroup.
resource "aws_db_parameter_group" "test_db_parameter_group" {
  name        = "${local.application_name}-${local.environment}-test-db-pg"
  family      = "mysql8.0"
  description = "Test DB parameter group for SCP validation"
}

# Validates SCP allows rds:CreateDBSnapshot.
resource "aws_db_snapshot" "test_db_snapshot" {
  db_instance_identifier = aws_db_instance.default.identifier
  db_snapshot_identifier = "${local.application_name}-${local.environment}-test-db-snapshot"
}

# Validates SCP allows rds:CreateDBShardGroup.
resource "aws_rds_shard_group" "test_db_shard_group" {
  db_shard_group_identifier = "${local.application_name}-${local.environment}-test-db-shard-group"
  db_cluster_identifier     = aws_rds_cluster.test_db_cluster.id
  max_acu                   = 64
}

# Validates SCP allows rds:CreateDBInstanceReadReplica.
resource "aws_db_instance" "test_read_replica" {
  identifier          = "${local.application_name}-${local.environment}-test-read-replica"
  instance_class      = "db.t3.micro"
  replicate_source_db = aws_db_instance.default.arn

  publicly_accessible    = false
  db_subnet_group_name   = aws_db_subnet_group.test_db_subnet_group.name
  vpc_security_group_ids = [aws_security_group.test_db_sg.id]
  skip_final_snapshot    = true
}

resource "aws_sns_topic" "test_rds_events" {
  name = "${local.application_name}-${local.environment}-test-rds-events"
}

# Validates SCP allows rds:CreateEventSubscription.
resource "aws_db_event_subscription" "test_db_event_subscription" {
  name             = "${local.application_name}-${local.environment}-test-db-event-subscription"
  sns_topic        = aws_sns_topic.test_rds_events.arn
  source_type      = "db-instance"
  source_ids       = [aws_db_instance.default.identifier]
  event_categories = ["availability"]
  enabled          = true
}

# Validates SCP allows rds:CreateOptionGroup.
resource "aws_db_option_group" "test_db_option_group" {
  name                     = "${local.application_name}-${local.environment}-test-db-option-group"
  option_group_description = "Test DB option group for SCP validation"
  engine_name              = "mysql"
  major_engine_version     = "8.0"
}

