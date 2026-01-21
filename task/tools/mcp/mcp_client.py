from typing import Any, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import (
    BlobResourceContents,
    CallToolResult,
    ReadResourceResult,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl

from task.tools.mcp.mcp_tool_model import MCPToolModel


class MCPClient:
    """Handles MCP server connection and tool execution"""

    def __init__(self, mcp_server_url: str) -> None:
        self.server_url = mcp_server_url
        self.session: Optional[ClientSession] = None
        self._streams_context = None
        self._session_context = None

    @classmethod
    async def create(cls, mcp_server_url: str) -> "MCPClient":
        """Async factory method to create and connect MCPClient"""
        # TODO:
        # 1. Create instance of MCPClient with `cls`
        instance = cls(mcp_server_url)
        # 2. Connect to MCP server
        await instance.connect()
        # 3. return created instance
        return instance

    async def connect(self):
        """Connect to MCP server"""
        # TODO:
        # 1. Check if session is present, if yes just return to finsh execution
        if self.session is not None:
            return
        # 2. Call `streamablehttp_client` method with `server_url` and set as `self._streams_context`
        self._streams_context = streamablehttp_client(self.server_url)
        # 3. Enter `self._streams_context`, result set as `read_stream, write_stream, _`
        read_stream, write_stream, _ = await self._streams_context.__aenter__()
        # 4. Create ClientSession with streams from above and set as `self._session_context`
        self._session_context = ClientSession(
            read_stream=read_stream, write_stream=write_stream
        )
        # 5. Enter `self._session_context` and set as self.session
        self.session = await self._session_context.__aenter__()
        # 6. Initialize session and print its result to console
        init_result = await self.session.initialize()
        print(f"MCP Client connected: {init_result}")

    async def get_tools(self) -> list[MCPToolModel]:
        """Get available tools from MCP server"""
        # TODO: Get and return MCP tools as list of MCPToolModel
        tools: list[MCPToolModel] = []
        tool_models = await self.session.list_tools()
        for tool_model in tool_models.tools:
            tools.append(
                MCPToolModel(
                    name=tool_model.name,
                    description=tool_model.description,
                    parameters=tool_model.inputSchema,
                )
            )
        return tools

    async def call_tool(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        """Call a tool on the MCP server"""
        # TODO: Make tool call and return its result. Do it in proper way (it returns array of content and you need to handle it properly)
        tool_call_result: CallToolResult = await self.session.call_tool(
            name=tool_name, arguments=tool_args
        )
        if tool_call_result.content:
            content = tool_call_result.content[0]
            if isinstance(content, TextContent):
                return content.text
            return content
        return None

    async def get_resource(self, uri: AnyUrl) -> str | bytes:
        """Get specific resource content"""
        # TODO: Get and return resource. Resources can be returned as TextResourceContents and BlobResourceContents, you
        #      need to return resource value (text or blob)
        resource_result: ReadResourceResult = await self.session.read_resource(uri=uri)
        if resource_result.contents:
            content = resource_result.contents[0]
            if isinstance(content, TextResourceContents):
                return content.text
            if isinstance(content, BlobResourceContents):
                return content.blob
            raise ValueError("Unsupported resource content type")
        raise ValueError(f"No content found for the given resource {uri}")

    async def close(self):
        """Close connection to MCP server"""
        # TODO:
        # 1. Close `self._session_context`
        if self._session_context is not None:
            await self._session_context.__aexit__(None, None, None)
        # 2. Close `self._streams_context`
        if self._streams_context is not None:
            await self._streams_context.__aexit__(None, None, None)
        # 3. Set session, _session_context and _streams_context as None
        self.session = None
        self._session_context = None
        self._streams_context = None

    async def __aenter__(self):
        """Async context manager entry"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()
        return False
