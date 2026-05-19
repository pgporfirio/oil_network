---
name: Master Thesis - Pedro Porfirio
description: Full context of Pedro's master thesis on vectorized temporal graph modeling of crude-oil logistics networks
type: project
---

## Title
**Vectorized Graph Modeling of Logistics Networks for Predictive Flow Optimization**

## Institution & Details
- NOVA Information Management School, Universidade Nova de Lisboa
- Master's in Information Management Systems
- Student: Pedro Porfirio (No. 20241283)
- Supervisor: Professor Flavio Pinheiro
- Date: March 2026

## Central Research Question
How can complex, interdependent crude-oil logistics systems be effectively represented as vectorized temporal graphs, and how can these representations be used to forecast inventory levels that target historical mean stock levels (mean reversion)?

## Three Core Objectives
1. Build a scalable graph-based model capturing structural dependencies AND multivariate time-series dynamics of the US crude-oil network
2. Identify the optimal vectorization method (node embeddings) for downstream ML forecasting — comparing node2vec/DeepWalk/LINE, GCN/GAT/GraphSAGE, TGAT/TGN/CAW, and hybrid approaches
3. Develop a forecasting framework targeting mean-reversion inventory levels, benchmarked against ARIMA, VAR, non-graph LSTM, MLP baselines

## Graph Schema Design (Chapter 3)

### Network Representation
- Directed, weighted temporal graph G(t) = (V, E, X(t), W(t))
- Nodes: Production sites, Storage facilities, Terminals (pass-through), Refineries, Consumption points
- Edges: exist **only** if a variable on node i references a variable on node j through a formula — topology is fully determined by variable formulas, never declared separately

### Uniform Feature Vector (key design decision)
Every node carries the same variables regardless of type:
`x(i,t) = [P(i,t), C(i,t), I(i,t), F_in(i,t), F_out(i,t), capacity_utilization]`
Inapplicable variables = 0 (e.g. storage node has P=0, C=0)

### Universal Mass Balance Constraint
`ΔS(i,g,t) = P(i,g,t) + F_in(i,g,t) − C(i,g,t) − F_out(i,g,t)`

### Balancing Item
Residual B(i,g,t) = observed ΔS − computed ΔS, retained as a node variable (follows IEA convention). Captures measurement error/unreported flows.

### Temporal Resolution
All variables in **barrels per day (b/d)**. Source data converted to average daily rates. Step-function convention between observations (no interpolation).

### Transit Time & Line Loss on Edges
Edge (i,j) carries: flow rate ϕ(i,j,g,t), transit time τ(i,j), line loss ℓ(i,j,g)
Conservation with transit: `I(i,t) = I(i,t−1) + P(i,t) + Σ_j f(j,i,t–τ(j,i)) − C(i,t) − Σ_k f(i,k,t)`

### Scope: US state-level nodes (50 states), EIA as primary data source

## Vectorization Methods (Chapter 4)

| Family | Methods | Temporal? | Inductive? |
|---|---|---|---|
| Structural | node2vec, DeepWalk, LINE | No | No |
| Spatial GNN | GCN, GAT, GraphSAGE | No (per-snapshot) | GraphSAGE yes |
| Temporal GNN | TGAT, TGN, CAW | Yes | Yes |
| Hybrid | GNN+LSTM/GRU, STGCN, Time2Vec+GNN | Yes | Varies |

**Preferred for inventory forecasting:** TGN (memory module per node captures cumulative inflow/outflow history — directly analogous to inventory tracking)

## Forecasting Framework (Chapter 5)

### Loss Function
`L = L_forecast + α·L_reversion + β·L_conservation`
- α: mean-reversion strength (speed of targeting historical mean)
- β: conservation constraint penalty
- Historical mean computed from training period only (no data leakage)

### Target
For each storage node i: I*(i) = Ā(I(i)) — historical average inventory
Forecast: flow adjustments Δf(i,j,t) that drive system toward target

### Training
- Rolling-window, chronological split (no temporal leakage)
- Adam optimizer + LR scheduling + early stopping
- Data augmentation: random edge dropout, temporal jittering

### Evaluation Metrics
- MAE, RMSE, MAPE (accuracy)
- MRR (Mean Reversion Rate) — fraction of nodes closer to mean at end of horizon
- CVR (Conservation Violation Rate) — fraction violating mass balance

### Baselines
ARIMA, VAR, non-graph LSTM, MLP

## Current Status of Notebooks
- `temporal_oil_network v1.0.ipynb`: Framework/architecture complete (50-state LocationDatabase, Flow, OilNetwork, TimeSeries classes). **No real data yet.** No flows defined.
- `get_eia_data_v3.ipynb`: EIA data retrieval (modified/in progress)

## Key Design Principles
1. **Formula-implies-edge**: edges never declared separately from formulas
2. **Separation of concerns**: topology (construction) vs. semantics (formulas) vs. validity (post-build checks)
3. **Uniform feature vectors**: prerequisite for standard GNN architectures
4. **Mixed-granularity handled naturally**: state-level vs PADD-level data handled via formula layer

## Why: Motivation
Crude-oil logistics is economically critical. Traditional OR (LP/MILP) and time-series methods (ARIMA/VAR) fail to capture nonlinear, time-dependent, spatially correlated network dynamics simultaneously. GNNs + temporal modeling bridge this gap.

## How to apply
When helping with code/analysis: always think in terms of the graph schema, mass balance constraint, and mean-reversion objective. The codebase is Python/Jupyter using pandas, numpy, networkx, statsmodels, and (planned) PyTorch Geometric or similar for GNNs. EIA is the data source.
