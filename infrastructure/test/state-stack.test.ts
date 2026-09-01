import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { GuardiaStateStack } from "../lib/state-stack";

function synth(): Template {
  const app = new cdk.App();
  const stack = new GuardiaStateStack(app, "TestStack", {
    env: { account: "111111111111", region: "sa-east-1" },
  });
  return Template.fromStack(stack);
}

test("checkpoints table has PK/SK keys, on-demand billing, and a TTL attribute", () => {
  const template = synth();
  template.hasResourceProperties("AWS::DynamoDB::Table", {
    TableName: "guardia-checkpoints",
    BillingMode: "PAY_PER_REQUEST",
    KeySchema: [
      { AttributeName: "PK", KeyType: "HASH" },
      { AttributeName: "SK", KeyType: "RANGE" },
    ],
    TimeToLiveSpecification: { AttributeName: "ttl", Enabled: true },
  });
});

test("incidents table exists with on-demand billing", () => {
  const template = synth();
  template.hasResourceProperties("AWS::DynamoDB::Table", {
    TableName: "guardia-incidents",
    BillingMode: "PAY_PER_REQUEST",
  });
});

test("runbooks bucket is versioned, encrypted, and blocks all public access", () => {
  const template = synth();
  template.hasResourceProperties("AWS::S3::Bucket", {
    VersioningConfiguration: { Status: "Enabled" },
    PublicAccessBlockConfiguration: {
      BlockPublicAcls: true,
      BlockPublicPolicy: true,
      IgnorePublicAcls: true,
      RestrictPublicBuckets: true,
    },
  });
});

test("model SSM parameters are declared", () => {
  const template = synth();
  template.hasResourceProperties("AWS::SSM::Parameter", { Name: "/guardia/model" });
  template.hasResourceProperties("AWS::SSM::Parameter", { Name: "/guardia/embedding-model" });
});
