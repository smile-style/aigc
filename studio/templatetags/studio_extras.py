import json

from django import template


register = template.Library()


@register.filter
def json_value(value):
    return json.dumps(value or {}, ensure_ascii=False, indent=2)


@register.filter
def platform_title_limit(platform):
    from studio.publishing.registry import get_platform_definition

    return get_platform_definition(platform).title_limit


@register.filter
def platform_requires_partition(platform):
    from studio.publishing.registry import get_platform_definition

    return get_platform_definition(platform).requires_partition
