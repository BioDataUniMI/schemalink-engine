import json
import os
import warnings
import networkx as nx
import matplotlib.pyplot as plt

def draw_dependency_graph(json_file, output_image="dependency_graph.png"):
    """
    Draw a cleaned and readable dependency graph based on class prompt dependencies.

    Args:
        json_file (str): Path to the JSON file containing class dependencies.
        output_image (str): Path to save the generated graph image.
    """

    # Resolve absolute paths
    json_file = os.path.abspath(json_file)
    output_image = os.path.abspath(output_image)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_image), exist_ok=True)

    # Load the JSON data
    with open(json_file, 'r') as file:
        data = json.load(file)
    
    # Create a directed graph
    G = nx.DiGraph()

    # Add nodes and edges
    for class_name, details in data.items():
        node_name = f"{class_name}Prompt"
        G.add_node(node_name, layer=1, color="green")  # Default: independent

        dependencies = details.get("dependencies", {})
        if isinstance(dependencies, dict):
            for dep_class in dependencies.values():
                dep_node_name = f"{dep_class}Prompt"
                G.add_node(dep_node_name)
                G.nodes[dep_node_name]["layer"] = 1
                G.nodes[dep_node_name]["color"] = "green"
                G.add_edge(dep_node_name, node_name)
                G.nodes[node_name]["layer"] = 2
                G.nodes[node_name]["color"] = "#ADD8E6"
        elif isinstance(dependencies, list):
            for dep_class in dependencies:
                dep_node_name = f"{dep_class}Prompt"
                G.add_node(dep_node_name)
                G.nodes[dep_node_name]["layer"] = 1
                G.nodes[dep_node_name]["color"] = "green"
                G.add_edge(dep_node_name, node_name)
                G.nodes[node_name]["layer"] = 2
                G.nodes[node_name]["color"] = "#ADD8E6"

    # Ensure all nodes have color and layer
    for node in G.nodes():
        G.nodes[node]["color"] = G.nodes[node].get("color", "#D3D3D3")
        G.nodes[node]["layer"] = G.nodes[node].get("layer", 1)

    node_colors = [G.nodes[node]["color"] for node in G.nodes()]

    # Use spring layout for clarity
    pos = nx.spring_layout(G, k=1.5, seed=42)

    # Clean up node labels
    labels = {
        node: node.replace("RelationshipPrompt", "RPrompt").replace("Prompt", "")
        for node in G.nodes()
    }

    # Draw the graph
    plt.figure(figsize=(16, 12))
    nx.draw(
        G, pos,
        with_labels=True,
        labels=labels,
        node_color=node_colors,
        node_size=1500,
        font_size=8,
        font_weight="bold",
        arrowsize=15,
        edge_color="gray"
    )

    plt.title("Prompt Dependency Graph", fontsize=16)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*tight_layout.*")
        plt.tight_layout()
    plt.savefig(output_image, dpi=300, bbox_inches='tight')
    # plt.show()
