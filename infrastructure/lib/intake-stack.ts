import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as cwActions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sns from "aws-cdk-lib/aws-sns";
import * as subs from "aws-cdk-lib/aws-sns-subscriptions";
import { Construct } from "constructs";
import { assertNoForbiddenActions } from "./iam-policy-guard";

export interface GuardiaIntakeStackProps extends cdk.StackProps {
  checkpointsTableArn: string;
  incidentsTableArn: string;
  checkpointsTableName: string;
  incidentsTableName: string;
}

/**
 * The subject-system Lambdas Guardia watches. Timeouts (seconds) are the
 * deployed values, used to derive the "close to timeout" duration alarm.
 * Only functions where a near-timeout run is an incident get one.
 */
export const WATCHED_FUNCTIONS: { name: string; timeoutSeconds: number; durationAlarm: boolean }[] = [
  { name: "photolist-analyze-photo", timeoutSeconds: 30, durationAlarm: true },
  { name: "photolist-generate-upload-url", timeoutSeconds: 10, durationAlarm: false },
  { name: "leaselens-analyze-contract", timeoutSeconds: 30, durationAlarm: true },
  { name: "leaselens-analyze-worker", timeoutSeconds: 300, durationAlarm: true },
  { name: "leaselens-extract-text", timeoutSeconds: 90, durationAlarm: false },
  { name: "leaselens-generate-upload-url", timeoutSeconds: 10, durationAlarm: false },
];

/** The intake Lambda only ever reads and writes single incident/dedup rows. */
export const INTAKE_DYNAMO_ACTIONS = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"];

export const DEDUP_WINDOW_SECONDS = 900;

/**
 * T5: CloudWatch alarm -> SNS -> intake Lambda -> incident record.
 *
 * There were no alarms on either subject system when this was built
 * (verified 2026-09-29), so this stack also creates them. They live here,
 * not in the PhotoList/LeaseLens stacks, so Guardia never edits the systems
 * it watches. Alarm names are prefixed photolist-/leaselens- because the
 * intake Lambda derives source_system from that prefix.
 */
export class GuardiaIntakeStack extends cdk.Stack {
  public readonly alarmTopic: sns.Topic;
  public readonly intakeFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: GuardiaIntakeStackProps) {
    super(scope, id, props);

    this.alarmTopic = new sns.Topic(this, "AlarmTopic", { topicName: "guardia-alarms" });

    // Its own role, not guardia-read-role: that role is for graph nodes and
    // deliberately has no log-write actions. This one can write three
    // single-row DynamoDB operations and its own logs, nothing else.
    this.intakeFunction = new lambda.Function(this, "IntakeFunction", {
      functionName: "guardia-intake",
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "lambda", "t5-intake"), {
        exclude: ["__pycache__", "*.pyc"],
      }),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "intake.handler",
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
      environment: {
        GUARDIA_INCIDENTS_TABLE: props.incidentsTableName,
        GUARDIA_CHECKPOINTS_TABLE: props.checkpointsTableName,
        GUARDIA_DEDUP_WINDOW_SECONDS: String(DEDUP_WINDOW_SECONDS),
      },
    });

    assertNoForbiddenActions(INTAKE_DYNAMO_ACTIONS, "guardia-intake");
    this.intakeFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "GuardiaIntakeRows",
        actions: INTAKE_DYNAMO_ACTIONS,
        resources: [props.incidentsTableArn, props.checkpointsTableArn],
      }),
    );

    this.alarmTopic.addSubscription(new subs.LambdaSubscription(this.intakeFunction));

    const alarmAction = new cwActions.SnsAction(this.alarmTopic);
    for (const fn of WATCHED_FUNCTIONS) {
      const dimensionsMap = { FunctionName: fn.name };
      const define = (
        suffix: string,
        metricName: string,
        statistic: string,
        threshold: number,
        description: string,
      ) => {
        const alarm = new cloudwatch.Alarm(this, `${fn.name}-${suffix}`, {
          alarmName: `${fn.name}-${suffix}`,
          alarmDescription: description,
          metric: new cloudwatch.Metric({
            namespace: "AWS/Lambda",
            metricName,
            dimensionsMap,
            statistic,
            period: cdk.Duration.minutes(5),
          }),
          threshold,
          evaluationPeriods: 1,
          comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
          treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        });
        alarm.addAlarmAction(alarmAction);
      };

      define("errors", "Errors", "Sum", 1, `${fn.name} reported at least one invocation error in 5 minutes`);
      define("throttles", "Throttles", "Sum", 1, `${fn.name} was throttled at least once in 5 minutes`);
      if (fn.durationAlarm) {
        // p99 at 80% of the configured timeout: a cold-start / slow-dependency
        // precursor that fires before invocations start timing out.
        define(
          "duration-p99",
          "Duration",
          "p99",
          fn.timeoutSeconds * 1000 * 0.8,
          `${fn.name} p99 duration reached 80% of its ${fn.timeoutSeconds}s timeout`,
        );
      }
    }
  }
}
