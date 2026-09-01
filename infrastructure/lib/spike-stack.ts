import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";

export interface GuardiaSpikeStackProps extends cdk.StackProps {
  /** Name of the existing GuardiaStateStack checkpoints table to reuse. */
  checkpointsTableName: string;
}

/**
 * T4 risk spike: a throwaway Lambda proving LangGraph can pause at
 * interrupt() across two separate invocations, resuming from a DynamoDB
 * checkpoint without re-running the node before the interrupt.
 *
 * Deliberately its own stack so it can be torn down independently of
 * GuardiaStateStack once the spike is validated (or kept, and evolved
 * into the T5+ intake Lambda — see docs/deviations for the T4 verdict).
 */
export class GuardiaSpikeStack extends cdk.Stack {
  public readonly spikeFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: GuardiaSpikeStackProps) {
    super(scope, id, props);

    const checkpointsTable = dynamodb.Table.fromTableArn(
      this,
      "ImportedCheckpointsTable",
      `arn:aws:dynamodb:${this.region}:${this.account}:table/${props.checkpointsTableName}`,
    );

    // Dependencies are pre-vendored into lambda/t4-spike/build (see
    // lambda/t4-spike/build.sh) rather than bundled by CDK, because CDK's
    // Docker-based Python bundling needs a running Docker daemon that this
    // dev machine doesn't have available.
    this.spikeFunction = new lambda.Function(this, "T4SpikeFunction", {
      functionName: "guardia-t4-spike",
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "lambda", "t4-spike", "build")),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "handler.handler",
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        GUARDIA_CHECKPOINTS_TABLE: props.checkpointsTableName,
      },
    });

    checkpointsTable.grantReadWriteData(this.spikeFunction);
  }
}
