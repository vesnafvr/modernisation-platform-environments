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
  availability_zones      = ["eu-west-2a", "eu-west-2b"]
  database_name           = "mydb"
  master_username         = "foo"
  master_password         = "must_be_eight_characters"
  backup_retention_period = 5
  preferred_backup_window = "07:00-09:00"

  storage_encrypted   = true

  db_subnet_group_name = aws_db_subnet_group.test_db_subnet_group.name
  vpc_security_group_ids = [aws_security_group.test_db_sg.id]
}

resource "aws_rds_global_cluster" "test_gloval_cluster" {
  global_cluster_identifier = "global-test"
  engine                    = "aurora"
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
  parameter_group_name = "default.mysql8.0"
  skip_final_snapshot  = true

  publicly_accessible = false
  storage_encrypted   = true

  db_subnet_group_name = aws_db_subnet_group.test_db_subnet_group.name
  vpc_security_group_ids = [aws_security_group.test_db_sg.id]
}