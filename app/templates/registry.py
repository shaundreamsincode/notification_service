"""Template registry: loads and validates ``templates/<slug>/<channel>.yaml``.

Both the API and the worker build one registry at startup (design D8). Any
problem in any template raises ``TemplateLoadError`` listing every problem
found, so a process never serves with a partial template set.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jinja2
import yaml
from jinja2 import meta
from jinja2.sandbox import ImmutableSandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field, create_model

CHANNELS = ("email", "sms")
OPTION_TYPES = ("string", "integer", "number", "boolean")

# Strict: no coercion ("3" is not an integer, 1 is not a boolean), and bool is
# never accepted where a number is expected.
_MODEL_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": int | float,
    "boolean": bool,
}

_PYTHON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}

# Body files beside the manifest, by channel: part name -> file suffix.
BODY_PARTS: dict[str, dict[str, str]] = {
    "email": {"html": ".html", "text": ".txt"},
    "sms": {"text": ".txt"},
}

_MISSING: Any = object()


class TemplateRenderError(Exception):
    """A render failed. The message names the part and, where known, the option,
    and never repeats an option value."""


@dataclass(frozen=True)
class RenderedMessage:
    subject: str | None
    html: str | None
    text: str


class TemplateLoadError(Exception):
    """One or more templates are invalid. The message names each problem."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("invalid templates:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class OptionSpec:
    name: str
    type: str
    required: bool
    default: Any = _MISSING

    @property
    def has_default(self) -> bool:
        return self.default is not _MISSING


def _field_name(option: str) -> str:
    # Prefixed so an option called "copy" or "json" cannot shadow a model attribute.
    return f"opt_{option}"


def build_options_model(options: dict[str, OptionSpec]) -> type[BaseModel]:
    """Pydantic model enforcing a variant's options contract."""
    fields: dict[str, Any] = {}
    for name, spec in options.items():
        annotation = _MODEL_TYPES[spec.type]
        if spec.required:
            default: Any = ...
        elif spec.has_default:
            default = spec.default
        else:
            # Never read: the field is excluded from the render context unless supplied.
            default = None
        fields[_field_name(name)] = (annotation, Field(default, alias=name))
    return create_model(
        "Options",
        __config__=ConfigDict(extra="forbid", strict=True, protected_namespaces=()),
        **fields,
    )


@dataclass(frozen=True)
class TemplateVariant:
    slug: str
    channel: str
    manifest_path: Path
    options: dict[str, OptionSpec]
    subject: str | None
    bodies: dict[str, str] = field(default_factory=dict)
    options_model: type[BaseModel] = field(default=BaseModel, compare=False, repr=False)
    # Compiled once at load, keyed "subject", "html", "text".
    templates: dict[str, jinja2.Template] = field(default_factory=dict, compare=False, repr=False)

    def validate_options(self, raw: Any) -> dict[str, Any]:
        """Check ``raw`` against the contract and return the render context.

        Raises ``pydantic.ValidationError``. The context holds supplied options
        and defaults only: an omitted optional option with no default stays
        undefined, so templates must guard it with ``is defined``.
        """
        model = self.options_model.model_validate(raw)
        context: dict[str, Any] = {}
        for name, spec in self.options.items():
            attr = _field_name(name)
            if attr in model.model_fields_set or spec.has_default:
                context[name] = getattr(model, attr)
        return context

    def render(self, context: dict[str, Any]) -> RenderedMessage:
        """Render every part. HTML is autoescaped; text, SMS and subjects are not.

        Raises ``TemplateRenderError``. Rendering reads nothing but ``context``,
        so equal contexts give identical output.
        """
        subject = None
        if "subject" in self.templates:
            subject = _render_part(self.templates["subject"], context, "subject").strip()
            _require_text(subject, "subject")
            if "\n" in subject or "\r" in subject:
                raise TemplateRenderError("subject: rendered with a line break")

        html = None
        if "html" in self.templates:
            html = _render_part(self.templates["html"], context, "html body")
            _require_text(html, "html body")

        text = _render_part(self.templates["text"], context, "text body")
        _require_text(text, "text body")
        return RenderedMessage(subject, html, text)


def make_environment(autoescape: bool) -> jinja2.Environment:
    """A hardened Jinja environment with no globals and strict undefined."""
    env = ImmutableSandboxedEnvironment(
        autoescape=autoescape,
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    # No range(), lipsum(), cycler() and friends: nothing non-deterministic,
    # and every name a template uses must be a declared option. The `random`
    # filter is the one built-in that would break determinism.
    env.globals.clear()
    del env.filters["random"]
    return env


_HTML_ENV = make_environment(autoescape=True)
_TEXT_ENV = make_environment(autoescape=False)


def _render_part(template: jinja2.Template, context: dict[str, Any], part: str) -> str:
    try:
        return template.render(context)
    except jinja2.UndefinedError as exc:
        # "'name' is undefined": names the option, carries no value.
        raise TemplateRenderError(f"{part}: {exc.message}") from None
    except Exception as exc:
        raise TemplateRenderError(f"{part}: render failed ({type(exc).__name__})") from None


def _require_text(value: str, part: str) -> None:
    if not value.strip():
        raise TemplateRenderError(f"{part}: rendered empty")


def _parse_options(raw: Any, problems: list[str], where: str) -> dict[str, OptionSpec]:
    if not isinstance(raw, dict):
        problems.append(f"{where}: 'options' must be a mapping of option name to its contract")
        return {}
    options: dict[str, OptionSpec] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name.isidentifier():
            problems.append(f"{where}: option name {name!r} is not a valid identifier")
            continue
        if not isinstance(spec, dict):
            problems.append(f"{where}: option '{name}' must be a mapping with a 'type'")
            continue
        type_name = spec.get("type")
        if type_name not in OPTION_TYPES:
            problems.append(
                f"{where}: option '{name}' has unsupported type {type_name!r} "
                f"(supported: {', '.join(OPTION_TYPES)})"
            )
            continue
        required = spec.get("required", True)
        if not isinstance(required, bool):
            problems.append(f"{where}: option '{name}' has a non-boolean 'required'")
            continue
        default = spec.get("default", _MISSING)
        if default is not _MISSING:
            if required:
                problems.append(f"{where}: option '{name}' is required and cannot have a default")
                continue
            ok = isinstance(default, _PYTHON_TYPES[type_name]) and not (
                type_name != "boolean" and isinstance(default, bool)
            )
            if not ok:
                problems.append(f"{where}: default for option '{name}' is not of type {type_name}")
                continue
        options[name] = OptionSpec(name, type_name, required, default)
    return options


def _undeclared(env: jinja2.Environment, source: str) -> set[str]:
    return meta.find_undeclared_variables(env.parse(source))


def _load_manifest(path: Path, problems: list[str]) -> TemplateVariant | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        problems.append(f"{path}: manifest is malformed ({type(exc).__name__})")
        return None
    if not isinstance(raw, dict):
        problems.append(f"{path}: manifest is malformed (expected a mapping)")
        return None

    slug, channel = raw.get("slug"), raw.get("channel")
    if not isinstance(slug, str) or not slug:
        problems.append(f"{path}: manifest is missing 'slug'")
        return None
    if channel not in CHANNELS:
        problems.append(f"template '{slug}' ({channel}) [{path}]: unsupported channel {channel!r}")
        return None
    where = f"template '{slug}' ({channel}) [{path}]"

    before = len(problems)
    options = _parse_options(raw.get("options"), problems, where)

    subject = raw.get("subject")
    if channel == "email" and (not isinstance(subject, str) or not subject.strip()):
        problems.append(f"{where}: email manifests must declare a 'subject'")
    if channel == "sms":
        subject = None

    bodies: dict[str, str] = {}
    for part, suffix in BODY_PARTS[channel].items():
        body_path = path.with_suffix(suffix)
        if not body_path.is_file():
            problems.append(f"{where}: missing body file {body_path.name}")
            continue
        bodies[part] = body_path.read_text(encoding="utf-8")

    if len(problems) > before:
        return None

    declared = set(options)
    # (part, label, source, environment)
    sources = [
        (part, f"body {path.with_suffix(BODY_PARTS[channel][part]).name}", source,
         _HTML_ENV if part == "html" else _TEXT_ENV)
        for part, source in bodies.items()
    ]  # fmt: skip
    if subject is not None:
        sources.append(("subject", "subject", subject, _TEXT_ENV))
    templates: dict[str, jinja2.Template] = {}
    for part, label, source, env in sources:
        try:
            # Autoescape does not change which variables a template references.
            names = _undeclared(_TEXT_ENV, source)
            templates[part] = env.from_string(source)
        except jinja2.TemplateSyntaxError as exc:
            # Includes TemplateAssertionError, e.g. an unknown filter.
            problems.append(f"{where}: {label} has a syntax error on line {exc.lineno}: {exc.message}")
            continue
        for name in sorted(names - declared):
            problems.append(f"{where}: {label} references undeclared variable '{name}'")

    if len(problems) > before:
        return None
    return TemplateVariant(
        slug, channel, path, options, subject, bodies, build_options_model(options), templates
    )


class TemplateRegistry:
    def __init__(self, variants: dict[tuple[str, str], TemplateVariant]):
        self._variants = variants

    @classmethod
    def load(cls, root: Path) -> "TemplateRegistry":
        if not root.is_dir():
            raise TemplateLoadError([f"template directory {root} does not exist"])

        problems: list[str] = []
        variants: dict[tuple[str, str], TemplateVariant] = {}
        for manifest in sorted(root.glob("*/*.yaml")):
            variant = _load_manifest(manifest, problems)
            if variant is None:
                continue
            # The path is the identity: templates/<slug>/<channel>.yaml. A
            # manifest declaring another slug or channel is a copy-paste
            # error, and would otherwise shadow or duplicate that variant.
            expected = root / variant.slug / f"{variant.channel}.yaml"
            if manifest != expected:
                problem = (
                    f"{manifest}: manifest declares slug '{variant.slug}' and channel "
                    f"'{variant.channel}', so it must live at {expected}"
                )
                if expected.is_file():
                    problem += f" (already defined by {expected})"
                problems.append(problem)
                continue
            variants[(variant.slug, variant.channel)] = variant

        if problems:
            raise TemplateLoadError(problems)
        return cls(variants)

    def get(self, slug: str, channel: str) -> TemplateVariant | None:
        return self._variants.get((slug, channel))

    def __len__(self) -> int:
        return len(self._variants)
