import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { GuardiaIamStack } from "../lib/iam-stack";
import { assertNoForbiddenActions, FORBIDDEN_ACTION_PATTERNS } from "../lib/iam-policy-guard";

const DUMMY_PROPS = {
  env: { account: "111111111111", region: "sa-east-1" },
  checkpointsTableArn: "arn:aws:dynamodb:sa-east-1:111111111111:table/guardia-checkpoints",
  incidentsTableArn: "arn:aws:dynamodb:sa-east-1:111111111111:table/guardia-incidents",
  runbooksBucketArn: "arn:aws:s3:::guardia-runbooks-111111111111-sa-east-1",
};

function synth(): Template {
  const app = new cdk.App();
  const stack = new GuardiaIamStack(app, "TestIamStack", DUMMY_PROPS);
  return Template.fromStack(stack);
}

/** Flatten every Action entry (string or string[]) out of every IAM policy statement in the template. */
function allGrantedActions(template: Template): string[] {
  const actions: string[] = [];
  const policies = template.findResources("AWS::IAM::Policy");
  for (const policy of Object.values(policies)) {
    const statements = (policy as any).Properties.PolicyDocument.Statement as any[];
    for (const statement of statements) {
      const action = statement.Action;
      if (Array.isArray(action)) {
        actions.push(...action);
      } else {
        actions.push(action);
      }
    }
  }
  return actions;
}

test("both execution roles exist", () => {
  const template = synth();
  template.hasResourceProperties("AWS::IAM::Role", { RoleName: "guardia-read-role" });
  template.hasResourceProperties("AWS::IAM::Role", { RoleName: "guardia-mutate-role" });
});

test("neither role's synthesized policy grants a forbidden action", () => {
  const template = synth();
  const actions = allGrantedActions(template);
  expect(actions.length).toBeGreaterThan(0);
  for (const action of actions) {
    for (const pattern of FORBIDDEN_ACTION_PATTERNS) {
      expect(action).not.toMatch(pattern);
    }
  }
});

test("mutate role's policies are scoped to photolist/leaselens resources, never '*'", () => {
  const template = synth();
  const mutatePolicy = Object.values(template.findResources("AWS::IAM::Policy")).find((p: any) =>
    JSON.stringify(p).includes("GuardiaMutateSubjectSystems"),
  ) as any;
  expect(mutatePolicy).toBeDefined();
  const statements = mutatePolicy.Properties.PolicyDocument.Statement as any[];
  const mutateStatement = statements.find((s) => s.Sid === "GuardiaMutateSubjectSystems");
  const resources = Array.isArray(mutateStatement.Resource) ? mutateStatement.Resource : [mutateStatement.Resource];
  for (const resource of resources) {
    expect(resource).not.toBe("*");
  }
});

// --- Proving the guard actually bites (T8 validation: "prove the test bites") ---

test("assertNoForbiddenActions throws when a Delete* action is present", () => {
  expect(() => assertNoForbiddenActions(["dynamodb:DeleteTable"], "guardia-read-role")).toThrow(/Delete/);
});

test("assertNoForbiddenActions throws when an IAM action is present", () => {
  expect(() => assertNoForbiddenActions(["iam:CreateUser"], "guardia-mutate-role")).toThrow(/iam:/);
});

test("assertNoForbiddenActions throws when a billing-write action is present", () => {
  expect(() => assertNoForbiddenActions(["aws-portal:ModifyBilling"], "guardia-mutate-role")).toThrow(/aws-portal/);
  expect(() => assertNoForbiddenActions(["budgets:ModifyBudget"], "guardia-mutate-role")).toThrow(/budgets/);
});

test("assertNoForbiddenActions is silent on the actual read/mutate action lists", () => {
  const { READ_INSPECTION_ACTIONS, MUTATE_ACTIONS } = require("../lib/iam-stack");
  expect(() => assertNoForbiddenActions(READ_INSPECTION_ACTIONS, "guardia-read-role")).not.toThrow();
  expect(() => assertNoForbiddenActions(MUTATE_ACTIONS, "guardia-mutate-role")).not.toThrow();
});

test("deliberately injecting a Delete* action into the real read-role action list fails the guard", () => {
  const { READ_INSPECTION_ACTIONS } = require("../lib/iam-stack");
  const tampered = [...READ_INSPECTION_ACTIONS, "logs:DeleteLogGroup"];
  expect(() => assertNoForbiddenActions(tampered, "guardia-read-role")).toThrow();
});
