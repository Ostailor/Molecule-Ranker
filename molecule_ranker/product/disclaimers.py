from __future__ import annotations

from molecule_ranker.product.schemas import ProductDisclaimer

REQUIRED_DISCLAIMER_PHRASES: tuple[str, ...] = (
    "research use only",
    "not medical advice",
    "not clinical decision support",
    "not a regulated medical product",
    "no patient treatment guidance",
    "no dosing",
    "no lab protocols",
    "no synthesis instructions",
    "no claims of cure",
    "no claims of safety",
    "no claims of efficacy",
    "no claims of activity",
    "no claims of binding",
    "no claims of manufacturability",
    "no claims of developability",
    "generated molecules and antibodies are computational hypotheses",
    "evidence and provenance should be independently reviewed",
    "complying with laws, institutional policies, and lab safety requirements",
)

IN_APP_DISCLAIMER_LOCATIONS: tuple[str, ...] = (
    "landing_page",
    "signup",
    "checkout_later",
    "dashboard",
    "run_creation",
    "generated_hypotheses",
    "result_export",
    "api_response",
)

RESEARCH_USE_ONLY_TEXT = (
    "Molecule Ranker is for research use only. It is not medical advice, not "
    "clinical decision support, and not a regulated medical product. It provides "
    "no patient treatment guidance, no dosing, no lab protocols, and no synthesis "
    "instructions. Molecule Ranker makes no claims of cure, no claims of safety, "
    "no claims of efficacy, no claims of activity, no claims of binding, no claims "
    "of manufacturability, and no claims of developability. Generated molecules "
    "and antibodies are computational hypotheses. Evidence and provenance should "
    "be independently reviewed. Users are responsible for complying with laws, "
    "institutional policies, and lab safety requirements."
)

GENERATED_HYPOTHESES_TEXT = (
    "Generated hypotheses, generated molecules, and generated antibodies are "
    "computational hypotheses for research use only. They are not medical advice, "
    "not clinical decision support, and not a regulated medical product. They "
    "provide no patient treatment guidance, no dosing, no lab protocols, and no "
    "synthesis instructions. They make no claims of cure, no claims of safety, no "
    "claims of efficacy, no claims of activity, no claims of binding, no claims of "
    "manufacturability, and no claims of developability. Evidence and provenance "
    "should be independently reviewed."
)

RESULT_EXPORT_TEXT = (
    "Result exports are research artifacts for human review. They are not medical "
    "advice, not clinical decision support, and not a regulated medical product. "
    "Exports provide no patient treatment guidance, no dosing, no lab protocols, "
    "and no synthesis instructions. Generated molecules and antibodies are "
    "computational hypotheses, and evidence and provenance should be independently "
    "reviewed before any downstream research decision."
)

API_RESPONSE_TEXT = (
    "API responses are for research use only and must not be used as medical "
    "advice, clinical decision support, patient treatment guidance, dosing, lab "
    "protocols, or synthesis instructions. Generated molecules and antibodies are "
    "computational hypotheses. Users are responsible for complying with laws, "
    "institutional policies, and lab safety requirements."
)

DEFAULT_PRODUCT_DISCLAIMERS: tuple[ProductDisclaimer, ...] = (
    ProductDisclaimer(
        disclaimer_id="research-use-landing-page",
        location="landing_page",
        text=RESEARCH_USE_ONLY_TEXT,
        required_acknowledgement=False,
    ),
    ProductDisclaimer(
        disclaimer_id="research-use-signup",
        location="signup",
        text=RESEARCH_USE_ONLY_TEXT,
        required_acknowledgement=True,
    ),
    ProductDisclaimer(
        disclaimer_id="research-use-checkout-later",
        location="checkout_later",
        text=RESEARCH_USE_ONLY_TEXT,
        required_acknowledgement=True,
        metadata={"payments_implemented": False},
    ),
    ProductDisclaimer(
        disclaimer_id="research-use-dashboard",
        location="dashboard",
        text=RESEARCH_USE_ONLY_TEXT,
        required_acknowledgement=True,
    ),
    ProductDisclaimer(
        disclaimer_id="research-use-run-creation",
        location="run_creation",
        text=RESEARCH_USE_ONLY_TEXT,
        required_acknowledgement=True,
    ),
    ProductDisclaimer(
        disclaimer_id="generated-hypotheses",
        location="generated_hypotheses",
        text=GENERATED_HYPOTHESES_TEXT,
        required_acknowledgement=True,
    ),
    ProductDisclaimer(
        disclaimer_id="result-export",
        location="result_export",
        text=RESULT_EXPORT_TEXT,
        required_acknowledgement=True,
    ),
    ProductDisclaimer(
        disclaimer_id="api-response",
        location="api_response",
        text=API_RESPONSE_TEXT,
        required_acknowledgement=False,
    ),
)


def default_product_disclaimers() -> list[ProductDisclaimer]:
    return [disclaimer.model_copy(deep=True) for disclaimer in DEFAULT_PRODUCT_DISCLAIMERS]


def disclaimer_locations() -> list[str]:
    return list(IN_APP_DISCLAIMER_LOCATIONS)


__all__ = [
    "API_RESPONSE_TEXT",
    "DEFAULT_PRODUCT_DISCLAIMERS",
    "GENERATED_HYPOTHESES_TEXT",
    "IN_APP_DISCLAIMER_LOCATIONS",
    "REQUIRED_DISCLAIMER_PHRASES",
    "RESEARCH_USE_ONLY_TEXT",
    "RESULT_EXPORT_TEXT",
    "default_product_disclaimers",
    "disclaimer_locations",
]
