export async function completeLifecycleSetup(page, setupPrompt, timeout) {
  const handleConfirmation = (dialog) => dialog.type() === "confirm" ? dialog.accept() : dialog.dismiss();
  page.on("dialog", handleConfirmation);
  try {
    const completion = page.waitForResponse((response) => {
      const request = response.request();
      return new URL(response.url()).pathname === "/api/state/save" &&
        request.method() === "POST" && Boolean(request.postDataJSON()?.state?.setupRun?.completedAt);
    }, { timeout });
    const [saved] = await Promise.all([completion, startLifecycleSetup(page, setupPrompt)]);
    const { status } = saved.request().postDataJSON().state.setupRun;
    if (!saved.ok() || (status !== "ok" && status !== "warning")) {
      throw new Error(`Setup did not complete successfully (status: ${status}, save HTTP ${saved.status()}).`);
    }
  } finally {
    page.off("dialog", handleConfirmation);
  }
}

async function startLifecycleSetup(page, setupPrompt) {
  const consent = await setupPrompt.getAttribute("value") === "accepted";
  await setupPrompt.click();
  if (!consent) return;

  await page.locator("[data-network-consent]").waitFor({ state: "detached", timeout: 30_000 });
  const settings = page.locator("[data-network-settings]");
  await settings.waitFor({ timeout: 30_000 });
  if (await settings.getAttribute("data-effective-network-mode") === "low") {
    await page.locator("[data-run-setup]").click();
  }
}
