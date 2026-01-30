from chains.end_chain import end_chain

from chains.controller_chain import run_controller_chain
from models import BrokerLogAnalysis


def route(analysis: BrokerLogAnalysis):
    if analysis.next_state == "CHECK_CONTROLLER":
        return run_controller_chain
    return end_chain
