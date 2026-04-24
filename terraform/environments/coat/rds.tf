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
  cluster_identifier      = "test_db_cluster"
  engine                  = "aurora-mysql"
  engine_version          = "5.7.mysql_aurora.2.03.2"
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