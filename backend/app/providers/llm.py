from typing import NewType

from dishka import Provider, Scope, provide
from langchain_aws import ChatBedrockConverse
from langchain_openai import AzureChatOpenAI

from app.llm_gw_settings import LLMGWSettings

# High enough cap to let grounded answers complete without truncating mid-citation.
CHAT_MAX_TOKENS = 4096

# Distinct types over the same underlying class so Dishka can resolve each gateway tier
# independently (it resolves by type). At runtime a NewType is just the wrapped object, so
# anything typed `ChatBedrockConverse` still accepts these.
SmallChatModel = NewType("SmallChatModel", ChatBedrockConverse)
MediumChatModel = NewType("MediumChatModel", ChatBedrockConverse)
BigChatModel = NewType("BigChatModel", ChatBedrockConverse)


class LLMProvider(Provider):
    """Shared chat models, reusable across features (not tied to the assistant).

    Exposes the LLM Gateway's three model tiers (small/medium/big) as distinct injectable
    types; each feature picks the tier it needs per call site.
    """

    scope = Scope.APP

    def _build(
        self, llm_gw_settings: LLMGWSettings, model_id: str
    ) -> ChatBedrockConverse:
        # Routed through the LLM Gateway's Bedrock-compatible /aws endpoint, like the batch
        # scripts: the single gateway API key doubles as the AWS access key (with a
        # placeholder secret). Stateless -> APP scope.
        return ChatBedrockConverse(
            endpoint_url=f"{llm_gw_settings.endpoint}/aws",
            aws_access_key_id=llm_gw_settings.llm_gw_key,
            aws_secret_access_key="placeholder",
            model_id=model_id,
            region_name="us-east-1",
            max_tokens=CHAT_MAX_TOKENS,
        )

    @provide
    def small_chat_model(self, llm_gw_settings: LLMGWSettings) -> SmallChatModel:
        # small_model = Claude Haiku 4.5.
        return SmallChatModel(self._build(llm_gw_settings, llm_gw_settings.small_model))

    @provide
    def medium_chat_model(self, llm_gw_settings: LLMGWSettings) -> MediumChatModel:
        # medium_model = Claude Sonnet 4.6.
        return MediumChatModel(
            self._build(llm_gw_settings, llm_gw_settings.medium_model)
        )

    @provide
    def big_chat_model(self, llm_gw_settings: LLMGWSettings) -> BigChatModel:
        # big_model = Claude Opus 4.8.
        return BigChatModel(self._build(llm_gw_settings, llm_gw_settings.big_model))


class EvaluationProvider(Provider):
    """LLM-as-judge model for the evaluation feature.

    Distinct from the gateway tiers above: the judge is an Azure OpenAI deployment
    (`LLMGWSettings.eval_model`, gpt-5.4) reached through the gateway's /foundry endpoint, the
    same surface as the embedding client — hence it reuses `LLMGWSettings.api_version`.
    `AzureChatOpenAI` is the only chat client of its type in the app, so it is injected by its
    plain type (no NewType needed).
    """

    scope = Scope.APP

    @provide
    def eval_model(self, llm_gw_settings: LLMGWSettings) -> AzureChatOpenAI:
        return AzureChatOpenAI(
            azure_endpoint=f"{llm_gw_settings.endpoint}/foundry",
            api_key=llm_gw_settings.llm_gw_key,
            api_version=llm_gw_settings.api_version,
            azure_deployment=llm_gw_settings.eval_model,
        )
