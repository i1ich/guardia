#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { GuardiaStateStack } from "../lib/state-stack";
import { GuardiaSpikeStack } from "../lib/spike-stack";

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: "sa-east-1",
};

const stateStack = new GuardiaStateStack(app, "GuardiaStateStack", { env });

new GuardiaSpikeStack(app, "GuardiaSpikeStack", {
  env,
  checkpointsTableName: stateStack.checkpointsTable.tableName,
});
