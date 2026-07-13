import os
import sys
import json
from pathlib import Path

# Add eval directory to path to reuse the routing, search, and generation logic
sys.path.insert(0, str(Path(__file__).parent.parent / "eval"))
from eval import route, semantic_search, filter_lookup, generate_answer

QUESTIONS = [
    "Who is the main protagonist of Attack on Titan and what can he transform into?",
    "List all Fantasy anime in the database with more than 50 episodes.",
    "What do people think of Demon Slayer's animation and pacing?",
    "which devil fruit did luffy eat",
    "List Adventure anime with more than 100 episodes."
]

def run_test():
    print("=" * 60)
    print("RUNNING CUSTOM SENPAI CONTEXT & ROUTING TESTS")
    print("=" * 60)
    
    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n[{i}] Question: {q}")
        
        # Route query to appropriate tool
        tool_call = route(q)
        called_name = tool_call["function"]["name"] if tool_call else None
        route_name = called_name if called_name in ("filter_lookup", "opinion_search") else "semantic_search"
        print(f"    Routed to: {route_name}")
        
        # Parse arguments
        args = json.loads(tool_call["function"]["arguments"]) if tool_call and tool_call["function"].get("arguments") else {}
        if args:
            print(f"    Tool arguments: {args}")
            
        # Retrieve context
        if route_name == "filter_lookup":
            results = filter_lookup(args)
            retrieved = [r["title"] for r in results]
        elif route_name == "opinion_search":
            results = semantic_search(q, source_filter="jikan_review")
            retrieved = [c["title"] for c in results]
        else:
            results = semantic_search(q)
            retrieved = [c["title"] for c in results]
            
        print(f"    Retrieved titles: {list(set(retrieved))}")
        
        # Generate final answer
        try:
            answer = generate_answer(q, route_name, results)
            print("-" * 60)
            print("Answer:")
            print(answer)
            print("=" * 60)
        except Exception as e:
            print(f"Error generating answer: {e}")

if __name__ == "__main__":
    run_test()
