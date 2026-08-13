from provenance import QueryLog
from db import DB
from verifier.judge.types import JudgeAgentOutput, JudgeScore
from planner import PlanAgentOutput


class JudgeAgent:
    def __init__(self, db: DB, query_log: QueryLog):
        self.db = db
        self.query_log = query_log
        # nodes = make_planner_nodes(db.get_engine())

        # self.builder = StateGraph(JudgeAgentState)
        # pass

    def judge(self, plan: PlanAgentOutput) -> JudgeAgentOutput:
        return JudgeAgentOutput(score=JudgeScore.VERY_UNCONFIDENT, reasoning="")
