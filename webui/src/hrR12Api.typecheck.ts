import { createHrR12Api } from "./hrR12Api";

const api = createHrR12Api("csrf");
const id = "00000000-0000-4000-8000-000000000001";

if (false) {
  // @ts-expect-error Candidate tasks require the candidate/relation pair and context.
  api.startTask(id, "candidate_match", id, { materialIds: [] });
  // @ts-expect-error Position tasks cannot carry a candidate relation.
  api.startTask(id, "jd", id, { contextVersionId: id, candidate: { candidateId: id, positionCandidateId: id } });
  // @ts-expect-error Comparison is only available through compareCandidates.
  api.startTask(id, "candidate_comparison", id, { contextVersionId: id, materialIds: [] });
}
