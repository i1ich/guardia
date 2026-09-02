/**
 * The blast-radius guardrail as an IAM fact, not a prompt promise (T8).
 *
 * Called at synth time from GuardiaIamStack, so `cdk synth`/`cdk deploy`
 * itself fails the build if a forbidden action is ever added to either
 * execution role. `infrastructure/test/iam-stack.test.ts` additionally
 * scans the synthesized template so the check survives even if a future
 * edit stops calling this function inline.
 */

export const FORBIDDEN_ACTION_PATTERNS: RegExp[] = [
  /:Delete/i, // no Delete* anywhere, in either role
  /^iam:/i, // no IAM write (or read) from either role
  /^aws-portal:/i, // no billing console access
  /^budgets:/i, // no billing write
  /^ce:/i, // no Cost Explorer write
  /^organizations:/i, // no AWS Organizations access
];

export function assertNoForbiddenActions(actions: string[], roleName: string): void {
  for (const action of actions) {
    for (const pattern of FORBIDDEN_ACTION_PATTERNS) {
      if (pattern.test(action)) {
        throw new Error(
          `IAM policy guard: role "${roleName}" grants forbidden action "${action}" ` +
            `(matches ${pattern}). Guardia execution roles may never hold Delete*, ` +
            `IAM access, or billing write.`,
        );
      }
    }
  }
}
