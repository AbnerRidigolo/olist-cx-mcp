from mcp import Client


async def chamar(servidor, ferramenta: str, **args):
    async with Client(servidor) as c:
        return await c.call_tool(ferramenta, args)


async def ok(servidor, ferramenta: str, **args) -> dict:
    r = await chamar(servidor, ferramenta, **args)
    assert not r.is_error, r.content[0].text
    assert r.content[0].text.strip(), "resumo em texto vazio"
    return r.structured_content


async def erro(servidor, ferramenta: str, **args) -> str:
    r = await chamar(servidor, ferramenta, **args)
    assert r.is_error, f"esperava erro, veio: {r.structured_content}"
    return r.content[0].text
