import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import {
  GuardiaInjectStack,
  INJECTABLE_TAG,
  INJECTION_TARGETS,
  functionName,
} from "../lib/inject-stack";

function synth(): Template {
  const app = new cdk.App();
  const stack = new GuardiaInjectStack(app, "TestInject", {
    env: { account: "111111111111", region: "sa-east-1" },
    alarmTopicArn: "arn:aws:sns:sa-east-1:111111111111:guardia-alarms",
  });
  return Template.fromStack(stack);
}

test("one disposable function and one alarm per injection kind", () => {
  const template = synth();
  expect(Object.keys(template.findResources("AWS::Lambda::Function"))).toHaveLength(
    INJECTION_TARGETS.length,
  );
  expect(Object.keys(template.findResources("AWS::CloudWatch::Alarm"))).toHaveLength(
    INJECTION_TARGETS.length,
  );
  for (const { kind } of INJECTION_TARGETS) {
    template.hasResourceProperties("AWS::Lambda::Function", { FunctionName: functionName(kind) });
  }
});

test("stub names never collide with the real subject functions", () => {
  for (const { kind } of INJECTION_TARGETS) {
    expect(functionName(kind)).toMatch(/^photolist-inject-/);
  }
});

test("every taggable resource carries the guardia-injectable tag", () => {
  const template = synth();
  const tag = { Key: INJECTABLE_TAG.key, Value: INJECTABLE_TAG.value };
  for (const type of ["AWS::Lambda::Function", "AWS::SSM::Parameter", "AWS::CloudWatch::Alarm"]) {
    for (const resource of Object.values(template.findResources(type))) {
      const tags = resource.Properties.Tags;
      if (type === "AWS::SSM::Parameter") {
        expect(tags).toMatchObject({ [INJECTABLE_TAG.key]: INJECTABLE_TAG.value });
      } else {
        expect(tags).toEqual(expect.arrayContaining([expect.objectContaining(tag)]));
      }
    }
  }
});

test("alarms use a 1-minute period and notify the intake topic", () => {
  const template = synth();
  for (const alarm of Object.values(template.findResources("AWS::CloudWatch::Alarm"))) {
    expect(alarm.Properties.Period).toBe(60);
    expect(alarm.Properties.AlarmActions).toEqual([
      "arn:aws:sns:sa-east-1:111111111111:guardia-alarms",
    ]);
  }
});

test("each function is wired to its injection kind", () => {
  const template = synth();
  for (const { kind } of INJECTION_TARGETS) {
    template.hasResourceProperties("AWS::Lambda::Function", {
      FunctionName: functionName(kind),
      Environment: { Variables: Match.objectLike({ INJECT_KIND: kind }) },
    });
  }
});
