import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import { assertNoForbiddenActions } from "./iam-policy-guard";

export interface GuardiaIamStackProps extends cdk.StackProps {
  checkpointsTableArn: string;
  incidentsTableArn: string;
  runbooksBucketArn: string;
}

/**
 * Read-only inspection actions available to every graph node except
 * `execute` (T6's seven tools + classify/plan/gather/hypothesize/etc).
 * Every action here is a Describe/Get/List/Query/Filter verb — nothing
 * that creates, updates, or deletes state — so a wildcard resource is a
 * reasonable scope for the *inspection* half; the *state* half (Dynamo,
 * S3) is tightened to specific ARNs below.
 */
export const READ_INSPECTION_ACTIONS = [
  // logs — query_logs (T6)
  "logs:StartQuery",
  "logs:GetQueryResults",
  "logs:StopQuery",
  "logs:FilterLogEvents",
  "logs:GetLogEvents",
  "logs:DescribeLogGroups",
  "logs:DescribeLogStreams",
  // cloudwatch — get_metrics (T6)
  "cloudwatch:GetMetricData",
  "cloudwatch:GetMetricStatistics",
  "cloudwatch:ListMetrics",
  "cloudwatch:DescribeAlarms",
  "cloudwatch:DescribeAlarmHistory",
  // cloudformation — recent_deployments, stack_resources (T6)
  "cloudformation:DescribeStacks",
  "cloudformation:DescribeStackEvents",
  "cloudformation:DescribeStackResources",
  "cloudformation:ListStackResources",
  "cloudformation:GetTemplate",
  // ssm — param_metadata (T6): metadata only. GetParameter is deliberately
  // absent, and so is GetParameterHistory — for String/StringList
  // parameters GetParameterHistory returns the plaintext historical
  // *values*, which would defeat the "never values" guarantee.
  "ssm:DescribeParameters",
  "ssm:ListTagsForResource",
  // lambda / apigateway / dynamodb / sqs / sns — dependency_probe, stack_resources (T6)
  "lambda:GetFunction",
  "lambda:GetFunctionConfiguration",
  "lambda:ListFunctions",
  "lambda:ListEventSourceMappings",
  "apigateway:GET",
  "dynamodb:DescribeTable",
  "sqs:GetQueueAttributes",
  "sns:GetTopicAttributes",
];

/** LangGraph checkpointer + incident-record persistence, needed by every node. */
export const CHECKPOINT_STATE_ACTIONS = [
  "dynamodb:GetItem",
  "dynamodb:PutItem",
  "dynamodb:UpdateItem",
  "dynamodb:Query",
];

/** Runbook corpus + embedding index (T17), read-only. */
export const RUNBOOK_READ_ACTIONS = ["s3:GetObject", "s3:ListBucket"];

/**
 * The only actions in the whole system reachable downstream of interrupt().
 * Scoped to the two subject stacks (photolist-latam, lease-lens) by
 * resource, not just by action — this role must not be able to touch
 * Guardia's own infrastructure or anything outside those two systems.
 */
export const MUTATE_ACTIONS = [
  "lambda:UpdateFunctionCode",
  "lambda:UpdateFunctionConfiguration",
  "lambda:InvokeFunction",
  "cloudformation:CreateChangeSet",
  "cloudformation:ExecuteChangeSet",
  "cloudformation:UpdateStack",
  "ssm:PutParameter",
];

/** Minimal read prerequisites `execute` needs to act idempotently. */
export const MUTATE_READ_PREREQ_ACTIONS = [
  "lambda:GetFunction",
  "lambda:GetFunctionConfiguration",
  "cloudformation:DescribeStacks",
];

export class GuardiaIamStack extends cdk.Stack {
  public readonly readRole: iam.Role;
  public readonly mutateRole: iam.Role;

  constructor(scope: Construct, id: string, props: GuardiaIamStackProps) {
    super(scope, id, props);

    // Trusted by the Lambda service (production) and by any principal in
    // this account (so a CI/admin identity can sts:AssumeRole to prove the
    // AccessDenied boundary in T8's integration test) — never by anything
    // outside the account.
    const trust = new iam.CompositePrincipal(
      new iam.ServicePrincipal("lambda.amazonaws.com"),
      new iam.AccountPrincipal(this.account),
    );

    const subjectFunctionArns = [
      `arn:aws:lambda:${this.region}:${this.account}:function:photolist-*`,
      `arn:aws:lambda:${this.region}:${this.account}:function:leaselens-*`,
    ];
    const subjectStackArns = [
      `arn:aws:cloudformation:${this.region}:${this.account}:stack/Photolist*/*`,
      `arn:aws:cloudformation:${this.region}:${this.account}:stack/LeaseLens*/*`,
    ];
    const subjectParamArns = [
      `arn:aws:ssm:${this.region}:${this.account}:parameter/photolist/*`,
      `arn:aws:ssm:${this.region}:${this.account}:parameter/leaselens/*`,
    ];

    // --- Read role: every node except execute -----------------------------
    this.readRole = new iam.Role(this, "ReadRole", {
      roleName: "guardia-read-role",
      assumedBy: trust,
      description:
        "Guardia: every graph node except execute. Read-only against sa-east-1, " +
        "plus checkpoint/incident/runbook state. No Delete*, no IAM, no billing.",
    });

    assertNoForbiddenActions(READ_INSPECTION_ACTIONS, "guardia-read-role");
    this.readRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "GuardiaReadOnlyInspection",
        actions: READ_INSPECTION_ACTIONS,
        resources: ["*"],
      }),
    );
    this.readRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "GuardiaCheckpointState",
        actions: CHECKPOINT_STATE_ACTIONS,
        resources: [
          props.checkpointsTableArn,
          props.incidentsTableArn,
          `${props.incidentsTableArn}/index/*`,
        ],
      }),
    );
    this.readRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "GuardiaRunbookRead",
        actions: RUNBOOK_READ_ACTIONS,
        resources: [props.runbooksBucketArn, `${props.runbooksBucketArn}/*`],
      }),
    );

    // --- Mutate role: execute node only, reachable only past interrupt() --
    this.mutateRole = new iam.Role(this, "MutateRole", {
      roleName: "guardia-mutate-role",
      assumedBy: trust,
      description:
        "Guardia: the execute node only. Scoped to photolist-latam and lease-lens " +
        "resources exclusively. No Delete*, no IAM, no billing.",
    });

    assertNoForbiddenActions(MUTATE_ACTIONS, "guardia-mutate-role");
    this.mutateRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "GuardiaMutateSubjectSystems",
        actions: [
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:InvokeFunction",
        ],
        resources: subjectFunctionArns,
      }),
    );
    this.mutateRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "GuardiaMutateStackDeploy",
        actions: ["cloudformation:CreateChangeSet", "cloudformation:ExecuteChangeSet", "cloudformation:UpdateStack"],
        resources: subjectStackArns,
      }),
    );
    this.mutateRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "GuardiaMutateParams",
        actions: ["ssm:PutParameter"],
        resources: subjectParamArns,
      }),
    );
    this.mutateRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "GuardiaMutateReadPrereqs",
        actions: MUTATE_READ_PREREQ_ACTIONS,
        resources: [...subjectFunctionArns, ...subjectStackArns],
      }),
    );
    this.mutateRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "GuardiaCheckpointStateForMutate",
        actions: CHECKPOINT_STATE_ACTIONS,
        resources: [
          props.checkpointsTableArn,
          props.incidentsTableArn,
          `${props.incidentsTableArn}/index/*`,
        ],
      }),
    );
  }
}
