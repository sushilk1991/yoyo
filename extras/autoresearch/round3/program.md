# Round three

Run only with `--pressure normal`. Stop on warning, critical, or unreadable pressure. Keep the existing one-child lock, memory limits, deadlines, weights, 0.90 score cutoff, and one-sided repetition policy.

Use all 135 previously evaluated cases for development. Freeze 60 new authored holdout cases before inference: six per label across the three tasks. The holdout rationales document intended labels; only `state` is shown to the model. These cases are smoke evidence, not independent production labels.

Test the existing seven proposals, the previous selected configuration, a variant preserving the original repetition prompt, and three variants that state missing-evidence criteria explicitly. Recombine the best development instruction per task once. Select with the unchanged rule: more correct raw labels, no task regression, no extra wrong suggestions, and no loss of correct suggestions relative to baseline.

Evaluate baseline and the development-selected candidate once on holdout. Also evaluate the frozen `previous_best` proposal on that same holdout after selection. This reference result must not affect selection or trigger another proposal. Compare both raw classifications and issued suggestions; do not compare percentages across different holdouts as if the data were identical.

The main search is limited to 180 seconds and each trial to 30 seconds. The reference check gets at most 30 seconds within the same outer 180-second round deadline. Every model process exits between trials. Do not weaken a gate or rerun holdout to choose another candidate. Save the result and leave installation separate from this experiment.
