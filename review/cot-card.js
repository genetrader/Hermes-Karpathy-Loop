function cotCard(props) {
  const cot = props.cot || {};
  const steps = cot.steps || [];
  // activity.py does not emit `live`; liveness is implied by having a task and
  // a recently-written log. Treat the log as live for 10 minutes.
  const live = (cot.task && (typeof cot.log_age_s !== "number" || cot.log_age_s < 600))
    ? { project: cot.project, task: cot.task } : null;

  let body;
  if (props.error) {
    body = jsx("div", { style: S.cotErr, children: String(props.error) });
  } else if (!steps.length) {
    body = jsx("div", { style: S.empty, children: live
      ? "waiting for the first step…"
      : "No round in flight. Press Start loop — steps appear here as the agent works." });
  } else {
    body = jsx("div", { style: S.cotScroll,
      children: steps.map(function (s, i) { return cotStep(s, i); }) });
  }

  return jsxs("div", { style: S.cotCard, children: [
    jsxs("div", { style: S.cotHead, children: [
      jsx("span", { style: S.cotTitle, children: "Chain of thought" }),
      jsx("span", { style: S.cotSub, children: live
        ? live.project + " \u00b7 " + live.task
          + (cot.steps_total ? " \u00b7 " + cot.steps_total + " steps" : "")
          + (typeof cot.log_age_s === "number"
              ? " \u00b7 updated " + ago(cot.log_age_s) : "")
        : "nothing in flight" }),
      jsx("span", { style: { marginLeft: "auto" },
        children: jsx(Button, {
          onClick: props.onRefresh, disabled: !!props.busy, children: "Refresh" }) }),
    ] }),
    body,
  ] });
}

/** One step: an italic reasoning card, or a monospace tool line. */
function cotStep(s, i) {
  const isThink = s.kind === "think";
  return jsxs("div", {
    style: isThink ? S.cotThink : S.cotStep,
    key: i,
  }, [
    jsx("span", { style: S.cotIcon,
      children: isThink ? "\u{1F9E0}" : (s.icon || "·") }),
    jsxs("div", { style: S.cotBody, children: [
      jsx("div", { style: isThink ? S.cotThinkText : S.cotStepText,
        children: shorten(isThink ? s.text : tidyPath(s.text),
                          isThink ? 320 : 150) }),
      jsxs("div", { style: S.cotMeta, children: [
        isThink ? "reasoning" : s.kind,
        s.duration ? " · " + s.duration : "",
        s.note ? " · " + shorten(s.note, 80) : "",
      ] }),
    ] }),
  ]);
}

