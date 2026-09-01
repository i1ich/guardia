import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { GuardiaSpikeStack } from "../lib/spike-stack";

function synth(): Template {
  const app = new cdk.App();
  const stack = new GuardiaSpikeStack(app, "TestSpikeStack", {
    env: { account: "111111111111", region: "sa-east-1" },
    checkpointsTableName: "guardia-checkpoints",
  });
  return Template.fromStack(stack);
}

test("spike function runs python3.12 and points at the checkpoints table", () => {
  const template = synth();
  template.hasResourceProperties("AWS::Lambda::Function", {
    FunctionName: "guardia-t4-spike",
    Handler: "handler.handler",
    Runtime: "python3.12",
    Environment: {
      Variables: { GUARDIA_CHECKPOINTS_TABLE: "guardia-checkpoints" },
    },
  });
});

test("IAM policy scopes DynamoDB access to the checkpoints table only, no wildcard resource", () => {
  const template = synth();
  const policies = template.findResources("AWS::IAM::Policy");
  const statements = Object.values(policies).flatMap(
    (p: any) => p.Properties.PolicyDocument.Statement,
  );
  const dynamoStatements = statements.filter((s: any) =>
    [s.Action].flat().some((a: string) => a?.startsWith("dynamodb:")),
  );
  expect(dynamoStatements.length).toBeGreaterThan(0);
  for (const statement of dynamoStatements) {
    expect(statement.Resource).not.toBe("*");
    expect(statement.Resource).not.toEqual(Match.arrayWith(["*"]));
  }
});
