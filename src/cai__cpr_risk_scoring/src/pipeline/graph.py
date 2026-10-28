from langchain_core.language_models import BaseChatModel

from src.agents.hcbi_policy_agent import HcbiPolicyAgent
from src.agents.policy_knowledge_agent import PolicyKnowledgeAgent
from src.agents.risk_classifier_agent import RiskClassifierAgent
from src.agents.risk_evaluation_agent import RiskEvaluationAgent
from src.agents.risk_writer_agent import RiskWriterAgent
from src.agents.scope_identifier_agent import ScopeIdentifierAgent
from src.agents.supporting_docs_agent import SupportingDocsAgent
from src.agents.transactional_data_agent import TransactionalDataAgent
from src.pipeline.supervisor import AuditSupervisor
from src.utils.logger import get_logger

logger = get_logger(__name__)


def build_audit_graph(
    llm: BaseChatModel,
    scope_identifier_agent: ScopeIdentifierAgent,
    hcbi_policy_agent: HcbiPolicyAgent,
    policy_knowledge_agent: PolicyKnowledgeAgent,
    supporting_docs_agent: SupportingDocsAgent,
    risk_evaluation_agent: RiskEvaluationAgent,
    transactional_data_agent: TransactionalDataAgent,
    risk_classifier_agent: RiskClassifierAgent,
    risk_writer_agent: RiskWriterAgent,
    tpi_inventory_block: str = "",
    transactional_context: str = "",
    recurrence_context: str = "",
    audit_context: str = "",
) -> AuditSupervisor:
    logger.info("[Graph] Building audit graph with 8 agents (scope_identifier + classifier + writer)")
    return AuditSupervisor(
        llm=llm,
        scope_identifier_agent=scope_identifier_agent,
        hcbi_policy_agent=hcbi_policy_agent,
        policy_knowledge_agent=policy_knowledge_agent,
        supporting_docs_agent=supporting_docs_agent,
        risk_evaluation_agent=risk_evaluation_agent,
        transactional_data_agent=transactional_data_agent,
        risk_classifier_agent=risk_classifier_agent,
        risk_writer_agent=risk_writer_agent,
        tpi_inventory_block=tpi_inventory_block,
        transactional_context=transactional_context,
        recurrence_context=recurrence_context,
        audit_context=audit_context,
    )
