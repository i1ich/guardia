import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as cwActions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sns from "aws-cdk-lib/aws-sns";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { Construct } from "constructs";

export interface GuardiaInjectStackProps extends cdk.StackProps {
  alarmTopicArn: string;
}

/** Every resource here carries this tag; the harness refuses anything without it. */
export const INJECTABLE_TAG = { key: "guardia-injectable", value: "true" };

export const HEALTHY_SEARCH_URL = "https://api.mercadolibre.com/sites/MLU/search";
export const TIMEOUT_SECONDS = 5;

type Signal = "errors" | "throttles" | "duration";

/** One disposable function per injection kind, each with the one alarm its failure should trip. */
export const INJECTION_TARGETS: { kind: string; signal: Signal }[] = [
  { kind: "token-expiry", signal: "errors" },
  { kind: "bad-param", signal: "errors" },
  { kind: "bad-deploy", signal: "errors" },
  { kind: "throttle", signal: "throttles" },
  { kind: "payload", signal: "duration" },
];

export const functionName = (kind: string) => `photolist-inject-${kind}`;
export const alarmName = (kind: string, signal: Signal) => `photolist-inject-${kind}-${signal}`;

/**
 * T15: disposable stub targets for the fault-injection harness. Nothing here
 * is PhotoList or LeaseLens: the stubs share only the `photolist-` name
 * prefix so the T5 intake Lambda accepts their alarms. Injections therefore
 * can never touch live traffic. Alarms use a 1-minute period so an injected
 * fault is visible in minutes, not the 5 of the production alarms.
 */
export class GuardiaInjectStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: GuardiaInjectStackProps) {
    super(scope, id, props);

    const topic = sns.Topic.fromTopicArn(this, "AlarmTopic", props.alarmTopicArn);
    const action = new cwActions.SnsAction(topic);

    const token = new ssm.StringParameter(this, "TokenParam", {
      parameterName: "/guardia-inject/token",
      stringValue: "valid-token-v1",
    });
    const searchUrl = new ssm.StringParameter(this, "SearchUrlParam", {
      parameterName: "/guardia-inject/search-url",
      stringValue: HEALTHY_SEARCH_URL,
    });

    for (const { kind, signal } of INJECTION_TARGETS) {
      const fn = new lambda.Function(this, `Fn-${kind}`, {
        functionName: functionName(kind),
        code: lambda.Code.fromAsset(path.join(__dirname, "..", "lambda", "inject-target"), {
          exclude: ["__pycache__", "*.pyc", "handler_bad.py"],
        }),
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: "handler.handler",
        timeout: cdk.Duration.seconds(TIMEOUT_SECONDS),
        memorySize: 128,
        environment: {
          INJECT_KIND: kind,
          TOKEN_PARAM: token.parameterName,
          SEARCH_URL_PARAM: searchUrl.parameterName,
        },
      });
      token.grantRead(fn);
      searchUrl.grantRead(fn);

      const metric = {
        errors: { name: "Errors", stat: "Sum", threshold: 1 },
        throttles: { name: "Throttles", stat: "Sum", threshold: 1 },
        duration: { name: "Duration", stat: "p99", threshold: TIMEOUT_SECONDS * 1000 * 0.8 },
      }[signal];
      const alarm = new cloudwatch.Alarm(this, `Alarm-${kind}`, {
        alarmName: alarmName(kind, signal),
        alarmDescription: `Fault-injection target ${functionName(kind)}: ${signal}`,
        metric: new cloudwatch.Metric({
          namespace: "AWS/Lambda",
          metricName: metric.name,
          dimensionsMap: { FunctionName: fn.functionName },
          statistic: metric.stat,
          period: cdk.Duration.minutes(1),
        }),
        threshold: metric.threshold,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });
      alarm.addAlarmAction(action);
    }

    cdk.Tags.of(this).add(INJECTABLE_TAG.key, INJECTABLE_TAG.value);
  }
}
