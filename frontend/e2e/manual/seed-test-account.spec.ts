import { writeFileSync } from "node:fs";

import { bootstrapRecovery, PASSWORD, test } from "../support";

// Not part of CI: run on demand with `E2E_MANUAL=1 SEED_ACCOUNT=1 npx playwright test seed-test-account`.
// Creates a clearly synthetic workspace through the real UI/API flow and saves the login to a git-ignored file.
test.skip(!process.env.SEED_ACCOUNT, "manual seed, set SEED_ACCOUNT=1");

test("seed audit test account", async ({ page, browser }) => {
  const stamp = Date.now().toString(36);
  const adminEmail = `qa.audit.${stamp}@veyra-test.example.test`;
  let mfa = { secret: "", codes: [] as string[] };

  await bootstrapRecovery(page, browser, {
    twoApprovers: true,
    adminEmail,
    approverAEmail: `qa.approver.a.${stamp}@veyra-test.example.test`,
    approverBEmail: `qa.approver.b.${stamp}@veyra-test.example.test`,
    workspace: "Audit Test Co (SYNTHETIC TEST DATA)",
    onAdminMfa: (m) => (mfa = m),
  });

  writeFileSync(
    "../seed-credentials.local.json",
    JSON.stringify(
      { adminEmail, password: PASSWORD, totpSecret: mfa.secret, recoveryCodes: mfa.codes },
      null,
      2,
    ),
  );
});
