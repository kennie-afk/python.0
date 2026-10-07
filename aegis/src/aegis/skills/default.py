"""A small default taxonomy so the skills endpoints work before a tenant defines its own.

It is deliberately modest (software and data roles, plus two people skills): a real deployment
supplies its own `taxonomy` in the request. Nothing here is a claim about what any role needs.
"""

from __future__ import annotations

from aegis.skills.taxonomy import Skill, SkillTaxonomy

DEFAULT_SKILLS: tuple[Skill, ...] = (
    Skill("python", frozenset({"py", "python3"}), "engineering"),
    Skill("java", frozenset({"jvm"}), "engineering"),
    Skill("typescript", frozenset({"ts"}), "engineering"),
    Skill("go", frozenset({"golang"}), "engineering"),
    Skill("sql", frozenset({"tsql", "plsql"}), "data"),
    Skill("postgresql", frozenset({"postgres", "psql"}), "data"),
    Skill("spark", frozenset({"pyspark"}), "data"),
    Skill("machine-learning", frozenset({"ml"}), "data"),
    Skill("kubernetes", frozenset({"k8s"}), "platform"),
    Skill("docker", frozenset({"containers"}), "platform"),
    Skill("terraform", frozenset({"iac"}), "platform"),
    Skill("aws", frozenset({"amazon-web-services"}), "platform"),
    Skill("testing", frozenset({"tdd", "qa"}), "practice"),
    Skill("security", frozenset({"appsec", "infosec"}), "practice"),
    Skill("leadership", frozenset({"mentoring"}), "management"),
    Skill("communication", frozenset({"presenting"}), "management"),
)


def default_taxonomy() -> SkillTaxonomy:
    return SkillTaxonomy(DEFAULT_SKILLS)
