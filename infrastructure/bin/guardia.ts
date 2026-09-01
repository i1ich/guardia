#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { GuardiaStateStack } from "../lib/state-stack";

const app = new cdk.App();

new GuardiaStateStack(app, "GuardiaStateStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: "sa-east-1",
  },
});
