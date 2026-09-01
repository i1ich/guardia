import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { Construct } from "constructs";

export class GuardiaStateStack extends cdk.Stack {
  public readonly checkpointsTable: dynamodb.Table;
  public readonly incidentsTable: dynamodb.Table;
  public readonly runbooksBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // LangGraph checkpointer state, one item per (thread, checkpoint).
    // Key names are PK/SK, not thread_id/checkpoint_id: this is a single-table
    // design shared with langgraph-checkpoint-aws's DynamoDBSaver, which
    // hardcodes those attribute names for both checkpoint and pending-writes
    // items (see T4 deviation notes). TTL matches the anti-decay window
    // rather than being kept forever.
    this.checkpointsTable = new dynamodb.Table(this, "CheckpointsTable", {
      tableName: "guardia-checkpoints",
      partitionKey: { name: "PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "SK", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "ttl",
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.incidentsTable = new dynamodb.Table(this, "IncidentsTable", {
      tableName: "guardia-incidents",
      partitionKey: { name: "incident_id", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.runbooksBucket = new s3.Bucket(this, "RunbooksBucket", {
      bucketName: `guardia-runbooks-${this.account}-${this.region}`,
      versioned: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    new ssm.StringParameter(this, "ModelParam", {
      parameterName: "/guardia/model",
      stringValue: "claude-sonnet-5",
    });

    new ssm.StringParameter(this, "EmbeddingModelParam", {
      parameterName: "/guardia/embedding-model",
      stringValue: "voyage-3-large",
    });
  }
}
