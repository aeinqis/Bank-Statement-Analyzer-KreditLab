# Extracted from app.py to keep the Streamlit entrypoint smaller.
from __future__ import annotations

import copy
import hashlib
import json
import re
import textwrap
from datetime import datetime
from html import escape
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    from core_utils import safe_float
except Exception:  # pragma: no cover - rebound from app.py during normal use
    safe_float = float


def bind_app_globals(app_globals: dict) -> None:
    """Expose app.py helpers/constants that these extracted functions already use."""
    for name, value in app_globals.items():
        if name.startswith("__") and name.endswith("__"):
            continue
        globals()[name] = value


def init_sidebar_navigation():
    """Initialize sidebar navigation state"""
    if "sidebar_collapsed" not in st.session_state:
        st.session_state.sidebar_collapsed = False
    
    if "active_section" not in st.session_state:
        st.session_state.active_section = "overview"


def toggle_sidebar():
    """Toggle sidebar collapsed state"""
    st.session_state.sidebar_collapsed = not st.session_state.sidebar_collapsed


def render_sidebar_navigation():
    """Render the collapsible sidebar navigation for Streamlit app"""
    results = st.session_state.get("results", [])
    company_name = st.session_state.get("company_name_override", "")
    if not company_name and results:
        for t in results:
            if t.get("company_name"):
                company_name = t["company_name"]
                break
    if not company_name:
        company_name = "Kredit Lab"

    nav_items = [
        {"id": "overview", "icon": "\U0001F3E0", "label": "Overview"},
        {"id": "extracted", "icon": "\U0001F4C4", "label": "Extracted Transactions"},
        {"id": "patterns", "icon": "\U0001F4CA", "label": "Pattern Analysis"},
        {"id": "counterparty", "icon": "\U0001F465", "label": "Counterparty Ledger"},
        {"id": "monthly", "icon": "\U0001F4C5", "label": "Monthly Summary"},
        {"id": "download", "icon": "\u2B07", "label": "Download Options"},
        {"id": "integrity", "icon": "\U0001F6E1", "label": "Document Integrity"},
    ]

    st.sidebar.markdown(f"### {company_name}")
    st.sidebar.caption("Statement Intelligence")
    st.sidebar.markdown("#### Navigation")

    has_results = bool(results)
    for item in nav_items:
        if not has_results and item["id"] not in ["overview", "download"]:
            continue
        st.sidebar.markdown(f'[{item["icon"]} {item["label"]}](#{item["id"]}-section)')

    st.sidebar.caption("v2.0.0")
    return False
    
    # Sidebar CSS - this creates a custom sidebar that overlays the Streamlit UI
    st.markdown("""
    <style>
        /* Hide the default Streamlit sidebar */
        section[data-testid="stSidebar"] {
            display: none !important;
        }
        
        /* Custom sidebar toggle button */
        .custom-sidebar-toggle {
            position: fixed;
            top: 70px;
            left: 0;
            z-index: 999;
            background: #0b0f19;
            border: 1px solid #1e2a42;
            border-left: none;
            border-radius: 0 8px 8px 0;
            color: #e2e8f0;
            cursor: pointer;
            padding: 10px 6px;
            font-size: 18px;
            transition: all 0.3s ease;
            box-shadow: 2px 0 10px rgba(0,0,0,0.3);
            width: 28px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .custom-sidebar-toggle:hover {
            background: #1a2235;
            width: 32px;
            border-color: #3b82f6;
        }
        
        /* Main sidebar container */
        .custom-sidebar {
            position: fixed;
            top: 0;
            left: 0;
            height: 100vh;
            width: 240px;
            background: #0b0f19;
            border-right: 1px solid #1e2a42;
            padding: 60px 0 20px 0;
            overflow-y: auto;
            overflow-x: hidden;
            z-index: 998;
            transition: transform 0.3s ease, width 0.3s ease;
            box-shadow: 2px 0 15px rgba(0,0,0,0.5);
        }
        
        .custom-sidebar.collapsed {
            transform: translateX(-210px);
            width: 240px;
        }
        
        .custom-sidebar.collapsed .nav-label {
            opacity: 0;
            max-width: 0;
            overflow: hidden;
            transition: opacity 0.2s ease, max-width 0.2s ease;
        }
        
        .custom-sidebar.collapsed .nav-item {
            padding: 10px 12px;
            justify-content: center;
        }
        
        .custom-sidebar.collapsed .nav-icon {
            margin-right: 0;
        }
        
        .custom-sidebar.collapsed .sidebar-company {
            padding: 8px 12px;
        }
        
        .custom-sidebar.collapsed .sidebar-company strong {
            font-size: 10px;
            text-align: center;
        }
        
        .custom-sidebar.collapsed .sidebar-company span {
            display: none;
        }
        
        .custom-sidebar.collapsed .sidebar-version {
            display: none;
        }
        
        /* Scrollbar styling */
        .custom-sidebar::-webkit-scrollbar {
            width: 4px;
        }
        
        .custom-sidebar::-webkit-scrollbar-track {
            background: transparent;
        }
        
        .custom-sidebar::-webkit-scrollbar-thumb {
            background: #1e2a42;
            border-radius: 2px;
        }
        
        .custom-sidebar::-webkit-scrollbar-thumb:hover {
            background: #334155;
        }
        
        /* Navigation items */
        .nav-item {
            display: flex;
            align-items: center;
            padding: 10px 16px;
            color: #94a3b8;
            text-decoration: none;
            cursor: pointer;
            border-left: 3px solid transparent;
            transition: all 0.2s ease;
            font-size: 13px;
            font-weight: 500;
            gap: 0;
            white-space: nowrap;
            border-radius: 0;
        }
        
        .nav-item:hover {
            background: #1a2235;
            color: #e2e8f0;
            border-left-color: #3b82f6;
        }
        
        .nav-item.active {
            background: #1a2235;
            color: #60a5fa;
            border-left-color: #3b82f6;
        }
        
        .nav-item .nav-icon {
            font-size: 18px;
            min-width: 28px;
            margin-right: 8px;
            flex-shrink: 0;
        }
        
        .nav-item .nav-label {
            transition: opacity 0.2s ease, max-width 0.2s ease;
            opacity: 1;
            max-width: 150px;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .nav-section-title {
            padding: 16px 16px 8px 16px;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #475569;
        }
        
        /* Company name in sidebar */
        .sidebar-company {
            padding: 12px 16px 8px 16px;
            border-bottom: 1px solid #1e2a42;
            margin-bottom: 4px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            transition: all 0.3s ease;
        }
        
        .sidebar-company strong {
            color: #e2e8f0;
            font-size: 14px;
            display: block;
            margin-bottom: 2px;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .sidebar-company span {
            color: #64748b;
            font-size: 10px;
            transition: opacity 0.3s ease;
        }
        
        .sidebar-version {
            position: absolute;
            bottom: 12px;
            left: 0;
            right: 0;
            text-align: center;
            font-size: 10px;
            color: #475569;
            padding: 8px;
            border-top: 1px solid #1e2a42;
        }
        
        /* Collapse button inside sidebar */
        .sidebar-collapse-btn {
            position: absolute;
            top: 12px;
            right: 12px;
            background: transparent;
            border: 1px solid #1e2a42;
            border-radius: 4px;
            color: #94a3b8;
            cursor: pointer;
            padding: 4px 8px;
            font-size: 14px;
            transition: all 0.2s ease;
            z-index: 1;
        }
        
        .sidebar-collapse-btn:hover {
            background: #1a2235;
            color: #e2e8f0;
            border-color: #3b82f6;
        }
        
        /* Bottom spacer */
        .sidebar-bottom-spacer {
            height: 60px;
        }
        
        /* Main content adjustment */
        .main-content-wrapper {
            margin-left: 240px;
            transition: margin-left 0.3s ease;
            padding: 0 20px 20px 20px;
        }
        
        .main-content-wrapper.collapsed {
            margin-left: 30px;
        }
        
        /* Responsive */
        @media (max-width: 768px) {
            .custom-sidebar {
                width: 200px;
                transform: translateX(-100%);
            }
            
            .custom-sidebar.mobile-open {
                transform: translateX(0);
            }
            
            .custom-sidebar.collapsed {
                transform: translateX(-100%);
            }
            
            .main-content-wrapper,
            .main-content-wrapper.collapsed {
                margin-left: 0;
                padding: 0 10px;
            }
        }
    </style>
    """, unsafe_allow_html=True)
    
    # Get company name for sidebar
    company_name = st.session_state.get("company_name_override", "")
    results = st.session_state.get("results", [])
    if not company_name and results:
        for t in results:
            if t.get("company_name"):
                company_name = t["company_name"]
                break
    
    if not company_name:
        company_name = "Kredit Lab"
    
    # Navigation items
    nav_items = [
        {"id": "overview", "icon": "🏠", "label": "Overview"},
        {"id": "extracted", "icon": "📄", "label": "Extracted Transactions"},
        {"id": "patterns", "icon": "📊", "label": "Pattern Analysis"},
        {"id": "counterparty", "icon": "👥", "label": "Counterparty Ledger"},
        {"id": "monthly", "icon": "📅", "label": "Monthly Summary"},
        {"id": "download", "icon": "⬇️", "label": "Download Options"},
        {"id": "integrity", "icon": "🛡️", "label": "Document Integrity"},
    ]
    
    # Check if results exist for conditional items
    has_results = bool(results)
    
    collapsed_class = "collapsed" if st.session_state.sidebar_collapsed else ""
    active_section = st.session_state.active_section
    
    # Build sidebar HTML
    nav_html = f'''
    <button class="custom-sidebar-toggle" onclick="toggleCustomSidebar()" title="Toggle Sidebar">
        {"" if st.session_state.sidebar_collapsed else "☰"}
    </button>
    
    <div class="custom-sidebar {collapsed_class}" id="customSidebar">
        <button class="sidebar-collapse-btn" onclick="toggleCustomSidebar()">
            {"" if st.session_state.sidebar_collapsed else "◀"}
        </button>
        
        <div class="sidebar-company">
            <strong>{company_name}</strong>
            <span>Statement Intelligence</span>
        </div>
        
        <div class="nav-section-title">Navigation</div>
    '''
    
    for item in nav_items:
        # Skip if no results and item requires results
        if not has_results and item["id"] not in ["overview", "download"]:
            continue
            
        active_class = "active" if active_section == item["id"] else ""
        nav_html += f'''
        <div class="nav-item {active_class}" onclick="navigateToSection('{item["id"]}')">
            <span class="nav-icon">{item["icon"]}</span>
            <span class="nav-label">{item["label"]}</span>
        </div>
        '''
    
    nav_html += '''
        <div class="sidebar-bottom-spacer"></div>
        <div class="sidebar-version">v2.0.0</div>
    </div>
    '''
    
    # JavaScript for sidebar interaction
    js = '''
    <script>
        function toggleCustomSidebar() {
            const sidebar = document.getElementById('customSidebar');
            if (sidebar) {
                sidebar.classList.toggle('collapsed');
                // Update the toggle button text
                const toggleBtn = document.querySelector('.custom-sidebar-toggle');
                if (toggleBtn) {
                    toggleBtn.textContent = sidebar.classList.contains('collapsed') ? '☰' : '◀';
                }
                // Send update to Streamlit
                const isCollapsed = sidebar.classList.contains('collapsed');
                const event = new CustomEvent('streamlit:setComponentValue', {
                    detail: { 
                        key: 'sidebar_collapsed',
                        value: isCollapsed 
                    }
                });
                document.dispatchEvent(event);
            }
        }
        
        function navigateToSection(sectionId) {
            // Update active state
            document.querySelectorAll('.nav-item').forEach(el => {
                el.classList.remove('active');
            });
            const clicked = document.querySelector(`.nav-item[onclick*="${sectionId}"]`);
            if (clicked) {
                clicked.classList.add('active');
            }
            
            // Scroll to the section
            const sectionMap = {
                'overview': 'overview-section',
                'extracted': 'extracted-section',
                'patterns': 'patterns-section',
                'counterparty': 'counterparty-section',
                'monthly': 'monthly-section',
                'download': 'download-section',
                'integrity': 'integrity-section'
            };
            
            const sectionId_map = sectionMap[sectionId];
            if (sectionId_map) {
                const element = document.getElementById(sectionId_map);
                if (element) {
                    const offset = 80;
                    const elementPosition = element.getBoundingClientRect().top;
                    const offsetPosition = elementPosition + window.pageYOffset - offset;
                    window.scrollTo({ top: offsetPosition, behavior: 'smooth' });
                }
            }
            
            // Send update to Streamlit
            const event = new CustomEvent('streamlit:setComponentValue', {
                detail: { 
                    key: 'active_section',
                    value: sectionId 
                }
            });
            document.dispatchEvent(event);
        }
        
        // Handle mobile
        function handleMobileSidebar() {
            const sidebar = document.getElementById('customSidebar');
            if (window.innerWidth <= 768 && sidebar) {
                if (!sidebar.classList.contains('collapsed')) {
                    sidebar.classList.add('mobile-open');
                }
            } else if (sidebar) {
                sidebar.classList.remove('mobile-open');
            }
        }
        
        window.addEventListener('resize', handleMobileSidebar);
        document.addEventListener('DOMContentLoaded', handleMobileSidebar);
    </script>
    '''
    
    st.markdown(textwrap.dedent(nav_html), unsafe_allow_html=True)
    st.markdown(textwrap.dedent(js), unsafe_allow_html=True)
    
    # Return the collapsed state
    return st.session_state.sidebar_collapsed


def get_main_content_class():
    """Get the CSS class for main content based on sidebar state"""
    if st.session_state.sidebar_collapsed:
        return "main-content-wrapper collapsed"
    return "main-content-wrapper"


__all__ = [
    'bind_app_globals',
    'init_sidebar_navigation',
    'toggle_sidebar',
    'render_sidebar_navigation',
    'get_main_content_class',
]
