# VigilZone — Design Guidelines

## Design Approach
**Utility-Focused Dashboard Design**: This is a professional community surveillance and security monitoring system requiring clarity, efficiency, and immediate comprehension. The design prioritizes data visibility, real-time information access, and quick decision-making over visual experimentation.

## Core Design Principles
- **Professional Dashboard Aesthetic**: Clean, modern interface that feels like a real production security product
- **Information Hierarchy**: Critical alerts and live data take visual priority
- **Consistent Component System**: Reusable cards, tables, and status indicators across all pages
- **Trust and Reliability**: Visual design communicates security, stability, and precision

## Typography System
**Font Family**: Roboto or Inter (sans-serif)
- **Page Titles**: 22-24px, bold weight
- **Section Headers**: 18-20px, semibold
- **Body Text**: 14-16px, regular weight
- **Data/Stats**: 16-18px, medium weight
- **Small Labels**: 12-14px, regular weight

## Layout & Spacing
**Spacing Units**: Use Tailwind units of 4, 5, 6, 8, 12, and 16 (p-4, m-8, gap-6, etc.)
- **Section Padding**: 20-32px between major sections (py-8 to py-12)
- **Card Padding**: 16-24px internal spacing (p-4 to p-6)
- **Component Gaps**: 16-20px between related elements (gap-4 to gap-5)
- **Page Margins**: Consistent 24-32px outer margins (px-6 to px-8)

## Component Library

### Navigation Bar
- Fixed top bar with logo/title (left), centered menu tabs (Dashboard | Cameras | Reports | Settings), profile avatar with username (right)
- Height: 64px with consistent horizontal padding
- Subtle bottom border or shadow for depth

### Cards & Containers
- **Card Style**: White background, rounded corners (rounded-lg or rounded-xl), subtle shadow (shadow-sm or shadow-md)
- **Alert Cards**: Colored left border accent based on severity (red for fire, orange for intrusion, yellow for warnings)
- **Data Cards**: Centered statistics with large numbers and descriptive labels below

### Tables
- **Header Row**: Gray background (#F9FAFB), semibold text
- **Data Rows**: Alternating subtle gray for better readability, hover state
- **Action Buttons**: Icon-based (Edit/Delete) with hover tooltips
- **Status Indicators**: Colored dot + text (🟢 Active green, 🔴 Offline red)

### Forms & Inputs
- **Input Fields**: Bordered with rounded corners, focused state with blue outline
- **Buttons**: 
  - Primary: Blue (#2563EB) background, white text, rounded, shadow on hover
  - Secondary: Gray border, transparent background, gray text
  - Critical: Red/Orange for destructive actions
- **Toggles/Switches**: Modern toggle switches for on/off settings
- **Dropdowns**: Clean select menus with chevron icons

### Charts & Visualizations
- **Chart Types**: Bar charts (incidents per day), Pie charts (anomaly breakdown), Line charts (trends)
- **Color Palette**: Blue primary, gray secondary, red/orange for critical metrics
- **Legends**: Clear, positioned consistently (top-right or bottom)
- **Grid Lines**: Subtle, gray, minimal

### Status & Badges
- **Active Status**: Green badge with rounded corners
- **Resolved/Inactive**: Gray badge
- **Critical Alerts**: Red badge with pulsing animation (subtle)
- **Confidence Scores**: Percentage with progress bar or colored background

## Page-Specific Layouts

### Login Page
- Centered card (max-width 400px), vertically and horizontally centered
- Title with optional shield/lock icon above
- Minimal form with generous field spacing (gap-6)
- Footer text in small, muted gray

### Dashboard (3-Column Layout)
- **Left Column (30%)**: Live camera feed thumbnails in 2x2 grid, play/pause controls
- **Middle Column (40%)**: Active alerts list (scrollable), incident summary stats cards, pie chart
- **Right Column (30%)**: System health metrics with small charts, numeric KPIs
- Responsive: Stack vertically on mobile/tablet

### Incident Details
- **Header**: Large incident number, status badge, timestamp
- **Split Layout**: 50/50 or 60/40 split
  - Left: Video snapshot with playback controls
  - Right: Data table (Type, Location, Confidence, Entities)
- **Action Buttons**: Primary "Acknowledge" and secondary "Export Report" prominently placed
- **Timeline**: Chronological log at bottom with time markers

### Reports Page
- **Filter Bar**: Horizontal layout with date picker, dropdown filters, export buttons
- **Charts Grid**: 2-column layout for charts on desktop, stack on mobile
- **Insight Box**: Highlighted summary box below charts with key findings

### Camera Management
- **Table Layout**: Full-width table with columns: Name, Location, Status, Added On, Actions
- **Add Camera Button**: Top-right, prominent blue button
- **Modal Form**: Centered overlay with fields for camera configuration, test connection feature

### Settings Page
- **Tab Navigation**: Horizontal tabs (Profile, Notifications, System Preferences)
- **Form Sections**: Grouped settings with clear section headers, generous vertical spacing
- **Save Buttons**: Sticky or prominently placed at bottom of each tab

## Visual Treatment
- **Shadows**: Use sparingly - cards get shadow-sm, modals get shadow-lg
- **Borders**: Subtle gray borders (border-gray-200) for separation
- **Rounded Corners**: Consistent rounded-lg (8px) for cards, rounded-md (6px) for buttons/inputs
- **Icons**: Simple, outlined style (Heroicons recommended), 20-24px for UI elements, 16px for inline

## Animations
**Minimal and Purposeful**:
- Hover states: Subtle scale or opacity change (transition-all duration-200)
- Critical alerts: Subtle pulse on new alert badge
- Modal/Dropdown: Fade-in with slight scale (duration-300)
- **No**: Page transitions, complex animations, parallax effects

## Images
**No hero images or marketing imagery** - this is a functional dashboard application. All visual content is system-generated (camera feeds, charts, thumbnails).

**Camera Feed Thumbnails**: 
- Placeholder images showing realistic security camera views (front door, living room, garage, backyard)
- Aspect ratio 16:9, displayed in grid format
- Include timestamp overlay on thumbnails