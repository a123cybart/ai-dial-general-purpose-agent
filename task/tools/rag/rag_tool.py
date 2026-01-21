import json
from typing import Any

import faiss
import numpy as np
from aidial_client import AsyncDial
from aidial_sdk.chat_completion import Message, Role
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer

from task.tools.base import BaseTool
from task.tools.models import ToolCallParams
from task.tools.rag.document_cache import DocumentCache
from task.utils.dial_file_conent_extractor import DialFileContentExtractor

# TODO: provide system prompt for Generation step
_SYSTEM_PROMPT = """
**Instructions:**  
You are an intelligent Retrieval-Augmented Generation (RAG) assistant. Your task is to answer user queries accurately by retrieving relevant information from provided documents, ensuring up-to-date and factual responses.

**Steps to Follow:**  
1. Retrieve the most pertinent, recent information relevant to the user's question from the available knowledge base.
2. Clearly base your answer on retrieved content; cite or reference when appropriate.
3. If information is unavailable, say so without fabricating facts.

**Constraints:**  
- Prioritize information retrieval accuracy.
- Do not guess or generate information not found in sources.
- Keep answers concise and informative.

**Request format:**
CONTEXT: <Relevant extracted document content>
QUESTION: <The user's question>
"""


class RagTool(BaseTool):
    """
    Performs semantic search on documents to find and answer questions based on relevant content.
    Supports: PDF, TXT, CSV, HTML.
    """

    def __init__(
        self, endpoint: str, deployment_name: str, document_cache: DocumentCache
    ):
        # TODO:
        # 1. Set endpoint
        self.endpoint = endpoint
        # 2. Set deployment_name
        self.deployment_name = deployment_name
        # 3. Set document_cache. DocumentCache is implemented, relate to it as to centralized Dict with file_url (as key),
        #    and indexed embeddings (as value), that have some autoclean. This cache will allow us to speed up RAG search.
        self.document_cache = document_cache
        # 4. Create SentenceTransformer and set is as `model` with:
        #   - model_name_or_path='all-MiniLM-L6-v2', it is self hosted lightwait embedding model.
        #     More info: https://medium.com/@rahultiwari065/unlocking-the-power-of-sentence-embeddings-with-all-minilm-l6-v2-7d6589a5f0aa
        #   - Optional! You can set it use CPU forcefully with `device='cpu'`, in case if not set up then will use GPU if it has CUDA cores
        self.model = SentenceTransformer(
            model_name_or_path="all-MiniLM-L6-v2",
            device="cpu",  # Uncomment to force CPU usage
        )
        # 5. Create RecursiveCharacterTextSplitter as `text_splitter` with:
        #   - chunk_size=500
        #   - chunk_overlap=50
        #   - length_function=len
        #   - separators=["\n\n", "\n", ". ", " ", ""]
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    @property
    def show_in_stage(self) -> bool:
        # TODO: set as False since we will have custom variant of representation in Stage
        return False

    @property
    def name(self) -> str:
        # TODO: provide self-descriptive name
        return "rag_search"

    @property
    def description(self) -> str:
        # TODO: provide tool description that will help LLM to understand when to use this tools and cover 'tricky'
        #  moments (not more 1024 chars)
        return "Performs semantic search on longer documents to find and answer questions based on relevant content. Supports: PDF, TXT, CSV, HTML."

    @property
    def parameters(self) -> dict[str, Any]:
        # TODO: provide tool parameters JSON Schema:
        #  - request is string, description: "The search query or question to search for in the document", required
        #  - file_url is string, required
        return {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "The search query or question to search for in the document.",
                },
                "file_url": {
                    "type": "string",
                    "description": "URL of the document to perform RAG search on.",
                },
            },
            "required": ["request", "file_url"],
        }

    async def _execute(self, tool_call_params: ToolCallParams) -> str | Message:
        # TODO:
        # 1. Load arguments with `json`
        args = json.loads(tool_call_params.tool_call.function.arguments)
        # 2. Get `request` from arguments
        request: str = args.get("request")
        # 3. Get `file_url` from arguments
        file_url: str = args.get("file_url")
        # 4. Get stage from `tool_call_params`
        stage = tool_call_params.stage
        # 5. Append content to stage: "## Request arguments: \n"
        stage.append_content("## Request arguments: \n")
        # 6. Append content to stage: `f"**Request**: {request}\n\r"`
        stage.append_content(f"**Request**: {request}\n\r")
        # 7. Append content to stage: `f"**File URL**: {file_url}\n\r"`
        stage.append_content(f"**File URL**: {file_url}\n\r")
        # 8. Create `cache_document_key`, it is string from `conversation_id` and `file_url`, with such key we guarantee
        #    access to cached indexes for one particular conversation,
        cache_document_key = f"{tool_call_params.conversation_id}_{file_url}"
        # 9. Get from `document_cache` by `cache_document_key` a cache
        cached_data = self.document_cache.get(cache_document_key)
        # 10. If cache is present then set it as `index, chunks = cached_data` (cached_data is retrieved cache from 9 step),
        if cached_data:
            index, chunks = cached_data
        #     otherwise:
        else:
            #       - Create DialFileContentExtractor and extract text by `file_url` as `text_content`
            extractor = DialFileContentExtractor(
                endpoint=self.endpoint, api_key=tool_call_params.api_key
            )
            text_content = extractor.extract_text(file_url=file_url)
            #       - If no `text_content` then appen to stage info about it ans return the string with the error that file content is not found
            if not text_content:
                stage.append_content("Error: File content not found.\n\r")
                return "Error: File content not found."
            #       - Create `chunks` with `text_splitter`
            chunks = self.text_splitter.split_text(text_content)
            #       - Create `embeddings` with `model`
            embeddings = self.model.encode(
                chunks, convert_to_numpy=True, show_progress_bar=False
            )
            #       - Create IndexFlatL2 with `384` dimensions as `index` (more about IndexFlatL2 https://shayan-fazeli.medium.com/faiss-a-quick-tutorial-to-efficient-similarity-search-595850e08473)
            index = faiss.IndexFlatL2(384)
            #       - Add to `index` np.array with created embeddings as type 'float32'
            index.add(np.array(embeddings).astype("float32"))
            #       - Add to `document_cache`
            self.document_cache.set(
                key=cache_document_key,
                index=index,
                chunks=chunks,
            )
        # 11. Prepare `query_embedding` with model. You need to encode request as type 'float32'
        query_embedding = self.model.encode(
            request, convert_to_numpy=True, show_progress_bar=False
        ).astype("float32")
        # 12. Through created index make search with `query_embedding`, `k` set as 3. As response we expect tuple of
        #     `distances` and `indices`
        distances, indices = index.search(np.array([query_embedding]), k=3)
        # 13. Now you need to iterate through `indices[0]` and and by each idx get element from `chunks`, result save as `retrieved_chunks`
        retrieved_chunks = [chunks[idx] for idx in indices[0]]
        # 14. Make augmentation
        augmented_prompt = self.__augmentation(request=request, chunks=retrieved_chunks)
        # 15. Append content to stage: "## RAG Request: \n"
        stage.append_content("## RAG Request: \n")
        # 16. Append content to stage: `ff"```text\n\r{augmented_prompt}\n\r```\n\r"` (will be shown as markdown text)
        stage.append_content(f"```text\n\r{augmented_prompt}\n\r```\n\r")
        # 17. Append content to stage: "## Response: \n"
        stage.append_content("## Response: \n")
        # 18. Now make Generation with AsyncDial (don't forget about api_version '025-01-01-preview, provide LLM with system prompt and augmented prompt and:
        #   - stream response to stage (user in real time will be able to see what the LLM responding while Generation step)
        #   - collect all content (we need to return it as tool execution result)
        dial_client = AsyncDial(
            base_url=self.endpoint,
            api_key=tool_call_params.api_key,
            api_version="2025-01-01-preview",
        )
        chunks = await dial_client.chat.completions.create(
            messages=[
                {"role": Role.SYSTEM, "content": _SYSTEM_PROMPT},
                {"role": Role.USER, "content": augmented_prompt},
            ],
            deployment_name=self.deployment_name,
            stream=True,
        )
        # 19. return collected content
        collected_content = ""
        async for chunk in chunks:
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                if delta.content:
                    stage.append_content(delta.content)
                    collected_content += delta.content
        return collected_content

    def __augmentation(self, request: str, chunks: list[str]) -> str:
        # TODO: make prompt augmentation
        return f"CONTEXT: {' '.join(chunks)}\nQUESTION: {request}"
