"""
Supabase client for database operations.
"""
from functools import lru_cache
from supabase import create_client, Client
from backend.config import get_settings


class SupabaseClient:
    """Wrapper around Supabase client with typed query methods."""

    def __init__(self, client: Client):
        self.client = client

    # =========================================================================
    # Parts queries
    # =========================================================================

    def get_part_by_ps_number(self, ps_number: str) -> dict | None:
        """Get a part by its PS number."""
        result = (
            self.client.table("parts")
            .select("*")
            .eq("ps_number", ps_number)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def find_part(
        self,
        query: str | None = None,
        appliance_type: str | None = None,
        part_type: str | None = None,
        brand: str | None = None,
        max_price: float | None = None,
        in_stock_only: bool = False,
        limit: int = 10
    ) -> dict:
        """
        Search for parts by manufacturer number, text, or filters.
        Returns PS numbers that can be used with get_part_by_ps_number().

        Search priority:
        1. Exact manufacturer part number match
        2. Partial manufacturer part number match
        3. Text search in part_name/description with filters
        """
        if not query and not any([appliance_type, part_type, brand]):
            return {"found": False, "message": "Please provide a search query or filters"}

        select_fields = (
            "ps_number, part_name, part_type, manufacturer_part_number, "
            "part_price, average_rating, availability, brand, appliance_type"
        )

        # Try manufacturer part number matches first (if query provided)
        if query:
            # Try exact manufacturer part number match
            result = (
                self.client.table("parts")
                .select(select_fields)
                .eq("manufacturer_part_number", query)
                .limit(1)
                .execute()
            )
            if result.data:
                return {
                    "found": True,
                    "match_type": "exact_manufacturer_number",
                    "count": 1,
                    "parts": result.data
                }

            # Try partial manufacturer part number match
            result = (
                self.client.table("parts")
                .select(select_fields)
                .ilike("manufacturer_part_number", f"%{query}%")
                .limit(5)
                .execute()
            )
            if result.data:
                return {
                    "found": True,
                    "match_type": "partial_manufacturer_number",
                    "count": len(result.data),
                    "parts": result.data
                }

        # Fall back to filtered text search
        q = self.client.table("parts").select(select_fields)

        if appliance_type:
            q = q.eq("appliance_type", appliance_type.lower())
        if part_type:
            q = q.ilike("part_type", f"%{part_type}%")
        if brand:
            q = q.ilike("brand", f"%{brand}%")
        if max_price:
            q = q.lte("part_price", max_price)
        if in_stock_only:
            q = q.eq("availability", "In Stock")
        if query:
            q = q.or_(f"part_name.ilike.%{query}%,part_description.ilike.%{query}%")

        result = q.limit(limit).execute()

        if result.data:
            return {
                "found": True,
                "match_type": "search",
                "count": len(result.data),
                "parts": result.data
            }

        return {"found": False, "message": "No parts found matching your criteria"}

    def search_parts(
        self,
        query: str | None = None,
        appliance_type: str | None = None,
        part_type: str | None = None,
        brand: str | None = None,
        max_price: float | None = None,
        in_stock_only: bool = False,
        limit: int = 10
    ) -> list[dict]:
        """Search parts with various filters."""
        q = self.client.table("parts").select(
            "ps_number, part_name, part_type, part_price, "
            "average_rating, num_reviews, availability, brand, appliance_type, "
            "part_url, manufacturer_part_number"
        )

        if appliance_type:
            q = q.eq("appliance_type", appliance_type.lower())
        if part_type:
            q = q.ilike("part_type", f"%{part_type}%")
        if brand:
            q = q.ilike("brand", f"%{brand}%")
        if max_price:
            q = q.lte("part_price", max_price)
        if in_stock_only:
            q = q.eq("availability", "In Stock")
        if query:
            # Search in part_name and part_description
            q = q.or_(f"part_name.ilike.%{query}%,part_description.ilike.%{query}%")

        result = q.limit(limit).execute()
        return result.data or []

    def validate_part(self, ps_number: str) -> dict:
        """Check if a PS number exists in the database."""
        result = (
            self.client.table("parts")
            .select("ps_number, part_name, availability")
            .eq("ps_number", ps_number)
            .limit(1)
            .execute()
        )
        if result.data:
            return {
                "found": True,
                "part_name": result.data[0].get("part_name"),
                "availability": result.data[0].get("availability")
            }
        return {"found": False}

    def find_by_manufacturer_number(self, manufacturer_number: str) -> dict | None:
        """Find a part by its manufacturer part number."""
        result = (
            self.client.table("parts")
            .select("ps_number, part_name, manufacturer_part_number, availability, appliance_type")
            .eq("manufacturer_part_number", manufacturer_number)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def find_by_manufacturer_number_partial(self, manufacturer_number: str, limit: int = 5) -> list[dict]:
        """Find parts by partial manufacturer part number match."""
        result = (
            self.client.table("parts")
            .select("ps_number, part_name, manufacturer_part_number, availability, appliance_type")
            .ilike("manufacturer_part_number", f"%{manufacturer_number}%")
            .limit(limit)
            .execute()
        )
        return result.data or []

    # =========================================================================
    # Model compatibility queries
    # =========================================================================

    def check_compatibility(self, ps_number: str, model_number: str) -> dict:
        """Check if a part is compatible with a model."""
        result = (
            self.client.table("model_compatibility")
            .select("*")
            .eq("part_id", ps_number)
            .eq("model_number", model_number)
            .limit(1)
            .execute()
        )
        if result.data:
            return {
                "compatible": True,
                "brand": result.data[0].get("brand"),
                "description": result.data[0].get("description")
            }
        return {"compatible": False}

    def get_compatible_parts(
        self,
        model_number: str,
        part_type: str | None = None,
        brand: str | None = None,
        limit: int = 200
    ) -> list[dict]:
        """Get all parts compatible with a model."""
        # First get compatible part IDs
        compat_result = (
            self.client.table("model_compatibility")
            .select("part_id")
            .eq("model_number", model_number)
            .execute()
        )

        if not compat_result.data:
            return []

        part_ids = [r["part_id"] for r in compat_result.data]

        # Then get part details
        q = (
            self.client.table("parts")
            .select("ps_number, part_name, part_type, part_price, average_rating, availability, part_url, manufacturer_part_number, num_reviews, brand")
            .in_("ps_number", part_ids)
        )

        if part_type:
            q = q.ilike("part_type", f"%{part_type}%")
        if brand:
            q = q.ilike("brand", f"%{brand}%")

        result = q.limit(limit).execute()
        return result.data or []

    def validate_model(self, model_number: str) -> dict:
        """Check if a model exists in our compatibility data."""
        result = (
            self.client.table("model_compatibility")
            .select("model_number, brand, description")
            .eq("model_number", model_number)
            .limit(1)
            .execute()
        )
        if result.data:
            return {
                "found": True,
                "brand": result.data[0].get("brand"),
                "description": result.data[0].get("description")
            }
        return {"found": False}

    def find_model_fuzzy(self, model_input: str, limit: int = 5) -> list[dict]:
        """Find models with fuzzy/partial matching."""
        # First try exact match (case-insensitive via ilike)
        result = (
            self.client.table("model_compatibility")
            .select("model_number, brand, description")
            .ilike("model_number", model_input)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data

        # Fall back to partial match
        result = (
            self.client.table("model_compatibility")
            .select("model_number, brand, description")
            .ilike("model_number", f"%{model_input}%")
            .limit(limit)
            .execute()
        )
        return result.data or []

    def get_compatible_models(
        self,
        ps_number: str,
        brand: str | None = None,
        limit: int = 5000
    ) -> list[dict]:
        """Get all models compatible with a specific part.

        Uses pagination to fetch all results despite Supabase's 1000-row limit per query.
        """
        print(f"[DEBUG] get_compatible_models called with limit={limit} for part {ps_number}")

        all_results = []
        batch_size = 1000  # Supabase max per query
        offset = 0

        while len(all_results) < limit:
            # Build query
            q = (
                self.client.table("model_compatibility")
                .select("model_number, brand, description")
                .eq("part_id", ps_number)
            )

            if brand:
                q = q.ilike("brand", f"%{brand}%")

            # Fetch batch with pagination
            result = q.range(offset, offset + batch_size - 1).execute()
            batch = result.data or []

            if not batch:
                break  # No more results

            all_results.extend(batch)
            print(f"[DEBUG] Fetched batch: offset={offset}, got {len(batch)} models, total so far: {len(all_results)}")

            # If we got fewer than batch_size, we've reached the end
            if len(batch) < batch_size:
                break

            offset += batch_size

        # Trim to requested limit
        final_results = all_results[:limit]
        print(f"[DEBUG] Database returned {len(final_results)} models for {ps_number} (after pagination)")
        return final_results

    # =========================================================================
    # Repair symptoms and instructions queries
    # =========================================================================

    def get_symptoms(self, appliance_type: str, symptom: str | None = None) -> list[dict]:
        """Get symptoms for an appliance type.

        If symptom is provided, uses LLM matching to find the best match.
        """
        result = (
            self.client.table("repair_symptoms")
            .select("symptom, symptom_description, percentage, video_url, symptom_url, parts, difficulty")
            .eq("appliance_type", appliance_type.lower())
            .order("percentage", desc=True)
            .execute()
        )

        all_symptoms = result.data or []

        if not symptom or not all_symptoms:
            return all_symptoms

        # Try exact substring match first
        for s in all_symptoms:
            if symptom.lower() in s["symptom"].lower():
                return [s]

        # Use LLM to find best match
        matched = self._llm_match_symptom(
            user_symptom=symptom,
            available_symptoms=[s["symptom"] for s in all_symptoms]
        )

        if matched:
            for s in all_symptoms:
                if s["symptom"] == matched:
                    return [s]

        # No match found, return empty
        return []

    def get_repair_instructions(
        self,
        appliance_type: str,
        symptom: str,
        part_type: str | None = None
    ) -> list[dict]:
        """Get repair instructions for a symptom using LLM-based matching.

        Uses a multi-stage approach:
        1. Try exact substring match
        2. Use LLM to find best matching symptom from available options
        """
        appliance_lower = appliance_type.lower()

        # First, try exact substring match
        symptom_result = (
            self.client.table("repair_symptoms")
            .select("symptom, video_url, symptom_url, difficulty")
            .eq("appliance_type", appliance_lower)
            .ilike("symptom", f"%{symptom}%")
            .limit(1)
            .execute()
        )

        if not symptom_result.data:
            # Get all symptoms and use LLM to find best match
            all_symptoms = (
                self.client.table("repair_symptoms")
                .select("symptom, video_url, symptom_url, difficulty")
                .eq("appliance_type", appliance_lower)
                .execute()
            )

            if all_symptoms.data:
                matched = self._llm_match_symptom(
                    user_symptom=symptom,
                    available_symptoms=[s["symptom"] for s in all_symptoms.data]
                )

                if matched:
                    # Find the full symptom record
                    for s in all_symptoms.data:
                        if s["symptom"] == matched:
                            symptom_result.data = [s]
                            break

        if not symptom_result.data:
            return {
                "instructions": [],
                "video_url": None,
                "symptom_url": None,
                "difficulty": None,
                "matched_symptom": None
            }

        matched_symptom = symptom_result.data[0]["symptom"]
        symptom_info = symptom_result.data[0]

        # Now get the instructions using the matched symptom
        q = (
            self.client.table("repair_instructions")
            .select("part_type, instructions, part_category_url")
            .eq("appliance_type", appliance_lower)
            .ilike("symptom", f"%{matched_symptom}%")
        )

        if part_type:
            q = q.ilike("part_type", f"%{part_type}%")

        result = q.execute()

        return {
            "instructions": result.data or [],
            "video_url": symptom_info.get("video_url"),
            "symptom_url": symptom_info.get("symptom_url"),
            "difficulty": symptom_info.get("difficulty"),
            "matched_symptom": matched_symptom
        }

    def _llm_match_symptom(self, user_symptom: str, available_symptoms: list[str]) -> str | None:
        """Use LLM to find the best matching symptom from available options."""
        import anthropic
        from backend.config import get_settings

        settings = get_settings()

        # Format options for the prompt
        options = "\n".join(f"- {s}" for s in available_symptoms)

        prompt = f"""Given the user's problem description, select the BEST matching symptom from the available options.

User's problem: "{user_symptom}"

Available symptom options:
{options}

Respond with ONLY the exact symptom text that best matches, or "NONE" if no option is relevant.
Your response must be exactly one of the options listed above (copy it exactly) or "NONE"."""

        try:
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=settings.HAIKU_MODEL,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}]
            )

            result = response.content[0].text.strip()

            # Validate the response is one of the options
            if result in available_symptoms:
                return result
            elif result == "NONE":
                return None
            else:
                # Try to find a close match (LLM might have slightly altered it)
                result_lower = result.lower()
                for s in available_symptoms:
                    if s.lower() == result_lower:
                        return s
                return None

        except Exception as e:
            print(f"  [WARN] LLM symptom matching failed: {e}")
            return None

    # =========================================================================
    # Vector search queries (semantic)
    # =========================================================================

    def search_qna(
        self,
        query_embedding: list[float],
        ps_number: str | None = None,
        match_threshold: float = 0.5,
        limit: int = 5
    ) -> list[dict]:
        """Search Q&A by semantic similarity, optionally filtered by part number."""
        try:
            # Debug logging
            print(f"  [DEBUG] search_qna called:")
            print(f"    embedding length: {len(query_embedding)}")
            print(f"    ps_number: {ps_number}")
            print(f"    match_threshold: {match_threshold}")
            print(f"    limit: {limit}")

            # Use the RPC function defined in schema.sql
            # filter_ps_number filters in the database BEFORE applying limit
            result = self.client.rpc(
                "search_qna",
                {
                    "query_embedding": query_embedding,
                    "match_threshold": match_threshold,
                    "match_count": limit,
                    "filter_ps_number": ps_number
                }
            ).execute()

            print(f"    results: {len(result.data) if result.data else 0}")
            return result.data or []
        except Exception as e:
            print(f"  [WARN] search_qna failed (table/RPC may not exist): {e}")
            return []

    def search_repair_stories(
        self,
        query_embedding: list[float],
        ps_number: str | None = None,
        match_threshold: float = 0.5,
        limit: int = 5
    ) -> list[dict]:
        """Search repair stories by semantic similarity, optionally filtered by part number."""
        try:
            # filter_ps_number filters in the database BEFORE applying limit
            result = self.client.rpc(
                "search_repair_stories",
                {
                    "query_embedding": query_embedding,
                    "match_threshold": match_threshold,
                    "match_count": limit,
                    "filter_ps_number": ps_number
                }
            ).execute()

            return result.data or []
        except Exception as e:
            print(f"  [WARN] search_repair_stories failed (table/RPC may not exist): {e}")
            return []

    def search_parts_semantic(
        self,
        query_embedding: list[float],
        appliance_type: str | None = None,
        match_threshold: float = 0.5,
        limit: int = 10
    ) -> list[dict]:
        """Search parts by semantic similarity.

        Use for natural language queries like "refrigerator bins" which would
        semantically match parts with part_type "Drawer or Glides".
        """
        try:
            result = self.client.rpc(
                "search_parts_semantic",
                {
                    "query_embedding": query_embedding,
                    "match_threshold": match_threshold,
                    "match_count": limit,
                    "filter_appliance_type": appliance_type.lower() if appliance_type else None
                }
            ).execute()

            return result.data or []
        except Exception as e:
            print(f"  [WARN] search_parts_semantic failed (table/RPC may not exist): {e}")
            return []

    def get_qna_by_ps_number(self, ps_number: str, limit: int = 10) -> list[dict]:
        """Get all Q&A for a specific part without semantic search."""
        try:
            result = (
                self.client.table("qna_embeddings")
                .select("question_id, question, answer, asker, date, model_number, helpful_count")
                .eq("ps_number", ps_number)
                .order("helpful_count", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            print(f"  [WARN] get_qna_by_ps_number failed (table may not exist): {e}")
            return []

    def get_repair_stories_by_ps_number(self, ps_number: str, limit: int = 10) -> list[dict]:
        """Get all repair stories for a specific part without semantic search."""
        try:
            result = (
                self.client.table("repair_stories_embeddings")
                .select("story_id, title, instruction, author, difficulty, repair_time, helpful_count, vote_count")
                .eq("ps_number", ps_number)
                .order("helpful_count", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            print(f"  [WARN] get_repair_stories_by_ps_number failed (table may not exist): {e}")
            return []

    def search_reviews(
        self,
        query_embedding: list[float],
        ps_number: str | None = None,
        match_threshold: float = 0.5,
        limit: int = 5
    ) -> list[dict]:
        """Search reviews by semantic similarity.

        Use for questions like "is this part easy to install?" or "any quality issues?"
        """
        try:
            result = self.client.rpc(
                "search_reviews",
                {
                    "query_embedding": query_embedding,
                    "match_threshold": match_threshold,
                    "match_count": limit,
                    "filter_ps_number": ps_number
                }
            ).execute()

            return result.data or []
        except Exception as e:
            print(f"  [WARN] search_reviews failed (table/RPC may not exist): {e}")
            return []

    def get_reviews_by_ps_number(self, ps_number: str, limit: int = 10) -> list[dict]:
        """Get all reviews for a specific part without semantic search."""
        try:
            result = (
                self.client.table("reviews_embeddings")
                .select("review_id, rating, title, content, author, date, verified_purchase")
                .eq("ps_number", ps_number)
                .order("rating", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            print(f"  [WARN] get_reviews_by_ps_number failed (table may not exist): {e}")
            return []


@lru_cache()
def get_supabase_client() -> SupabaseClient:
    """Get cached Supabase client instance."""
    settings = get_settings()
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return SupabaseClient(client)
