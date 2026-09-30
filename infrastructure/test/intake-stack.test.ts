import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { GuardiaIntakeStack, WATCHED_FUNCTIONS } from "../lib/intake-stack";
import { FORBIDDEN_ACTION_PATTERNS } from "../lib/iam-policy-guard";

function synth(): Template {
  const app = new cdk.App();
  const stack = new GuardiaIntakeStack(app, "TestIntake", {
    env: { account: "111111111111", region: "sa-east-1" },
    checkpointsTableArn: "arn:aws:dynamodb:sa-east-1:111111111111:table/guardia-checkpoints",
    checkpointsTableName: "guardia-checkpoints",
    incidentsTableArn: "arn:aws:dynamodb:sa-east-1:111111111111:table/guardia-incidents",
    incidentsTableName: "guardia-incidents",
  });
  return Template.fromStack(stack);
}

test("SNS topic is subscribed to the intake Lambda", () => {
  const template = synth();
  template.hasResourceProperties("AWS::SNS::Topic", { TopicName: "guardia-alarms" });
  template.hasResourceProperties("AWS::SNS::Subscription", { Protocol: "lambda" });
});

test("every alarm name carries a subject-system prefix and publishes to the topic", () => {
  const template = synth();
  const alarms = template.findResources("AWS::CloudWatch::Alarm");
  expect(Object.keys(alarms).length).toBe(15); // 6 errors + 6 throttles + 3 duration
  for (const alarm of Object.values(alarms)) {
    expect(alarm.Properties.AlarmName).toMatch(/^(photolist|leaselens)-/);
    expect(alarm.Properties.AlarmActions).toHaveLength(1);
    expect(alarm.Properties.TreatMissingData).toBe("notBreaching");
  }
});

test("duration alarm sits at 80% of the deployed timeout", () => {
  const template = synth();
  const worker = WATCHED_FUNCTIONS.find((f) => f.name === "leaselens-analyze-worker")!;
  template.hasResourceProperties("AWS::CloudWatch::Alarm", {
    AlarmName: "leaselens-analyze-worker-duration-p99",
    ExtendedStatistic: "p99",
    Threshold: worker.timeoutSeconds * 1000 * 0.8,
  });
});

test("intake Lambda's own statements hold no forbidden action and only three DynamoDB verbs", () => {
  const template = synth();
  const policies = template.findResources("AWS::IAM::Policy");
  const statements = Object.values(policies).flatMap((p: any) => p.Properties.PolicyDocument.Statement);
  const actions: string[] = statements.flatMap((s: any) => [].concat(s.Action));
  for (const action of actions) {
    for (const pattern of FORBIDDEN_ACTION_PATTERNS) {
      expect(action).not.toMatch(pattern);
    }
  }
  const rows = statements.find((s: any) => s.Sid === "GuardiaIntakeRows");
  expect(rows.Action).toEqual(["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]);
  expect(rows.Resource).not.toContain("*");
});

test("function receives table names and the dedup window", () => {
  const template = synth();
  template.hasResourceProperties("AWS::Lambda::Function", {
    FunctionName: "guardia-intake",
    Handler: "intake.handler",
    Environment: {
      Variables: Match.objectLike({
        GUARDIA_INCIDENTS_TABLE: "guardia-incidents",
        GUARDIA_CHECKPOINTS_TABLE: "guardia-checkpoints",
        GUARDIA_DEDUP_WINDOW_SECONDS: "900",
      }),
    },
  });
});
