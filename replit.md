# Secure Cloud Intelligence

## Overview

Secure Cloud Intelligence is a community-based smart surveillance web application that provides real-time anomaly detection from live video streams. The system features context-aware security monitoring with entity recognition, incident management, and community-focused privacy controls. Built as a full-stack TypeScript application with React frontend and Express backend, it emphasizes professional dashboard design, real-time data visualization, granular privacy settings, and a complete light/dark theme system for shared surveillance scenarios.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend Architecture

**Framework & Build System**
- React with TypeScript for type-safe component development
- Vite as the build tool and development server
- Wouter for client-side routing (lightweight React Router alternative)
- TanStack Query (React Query) for server state management and caching

**UI Component System**
- Shadcn/ui component library based on Radix UI primitives
- Tailwind CSS for utility-first styling with custom design tokens
- Component theming via CSS variables for both light and dark modes
- Theme switching system with React Context and localStorage persistence
- Custom design system following professional dashboard aesthetics with rounded corners, card-based layouts, and consistent spacing
- Responsive mobile navigation with hamburger menu and slide-out drawer

**State Management Approach**
- React Query handles all server state with infinite stale time (manual refetching)
- Local component state via React hooks for UI interactions
- No global state management library (Redux/Zustand) - keeping it simple

**Key Design Patterns**
- Reusable card components (AlertCard, CameraFeed, StatsCard) for consistent UI
- Layout composition with authenticated vs. unauthenticated routes
- Data visualization using Recharts for analytics (pie charts, line charts, bar charts)
- Form handling with React Hook Form and Zod validation

### Backend Architecture

**Server Framework**
- Express.js with TypeScript for the REST API
- HTTP server creation via Node's built-in `http` module
- Custom Vite middleware integration for development mode
- Simplified routing structure (routes defined in `server/routes.ts`)

**Data Storage Strategy**
- Drizzle ORM configured for PostgreSQL as the target database
- Schema-first approach with TypeScript types generated from Drizzle schema
- In-memory storage implementation (`MemStorage`) for development/testing
- Storage interface pattern (`IStorage`) allows swapping between memory and database implementations

**Database Schema Design**
- **Users**: Authentication and profile management
- **Zones**: Privacy-aware area definitions (home/street/shared) with consent controls
- **Entities**: Recognized persons, pets, vehicles with group classifications (household/neighbor/watchlist)
- **Cameras**: Video feed management with zone associations and status tracking
- **Incidents**: Anomaly detection records with type classification, confidence scores, and entity linking

**API Architecture**
- RESTful endpoints prefixed with `/api`
- Centralized error handling and request logging middleware
- JSON request/response format
- CRUD operations abstracted through storage interface

### Authentication & Session Management

**Current Implementation**
- Basic login flow without actual authentication (placeholder)
- Session management configured via `connect-pg-simple` for PostgreSQL-backed sessions
- Cookie-based session persistence

**Security Considerations**
- Password hashing not yet implemented (schema includes password field)
- No JWT or OAuth integration currently
- Sessions intended to be stored in PostgreSQL once database is connected

### Privacy & Consent Architecture

**Zone-Based Privacy Controls**
- Three zone types: Home (private), Street (shared with neighborhood), Shared (community accessible)
- Per-zone settings for face blurring, audio disabling, and incident-only sharing
- Entity consent tracking with `consentObtained` boolean flag

**Community Sharing Model**
- Role-based access (owner/admin/member/viewer) for community members
- Camera sharing controlled at zone level
- Incident data can be restricted vs. full feed sharing

### Data Visualization & Analytics

**Dashboard Metrics**
- Real-time KPI cards (incidents today, active alerts, detection latency, uptime)
- Incident type breakdown via pie charts
- Temporal trends with line charts (latency over time, response trends)
- Daily incident volume via bar charts

**Filtering & Search**
- Multi-dimensional filtering (date range, camera, zone, entity type, incident type)
- Search functionality across incidents, entities, and cameras
- Filter state managed locally in components

### Asset Management

**Static Assets**
- Generated camera images stored in `attached_assets/generated_images/`
- Vite alias `@assets` for importing images
- Design guidelines and requirements stored as text files in `attached_assets/`

### Development Workflow

**Build & Deployment**
- Development: `tsx` executes server directly with Vite middleware
- Production: Vite builds client, esbuild bundles server into `dist/`
- Database migrations via `drizzle-kit push`
- TypeScript type checking with `tsc --noEmit`

**Code Organization**
- Monorepo structure with `client/`, `server/`, and `shared/` directories
- Path aliases configured for clean imports (`@/`, `@shared/`, `@assets/`)
- Shared schema and types between frontend and backend via `shared/schema.ts`

## External Dependencies

### Database
- **PostgreSQL** (configured but not yet connected): Primary data store
- **Neon Serverless PostgreSQL Driver** (`@neondatabase/serverless`): Cloud-native Postgres client
- **Drizzle ORM** (`drizzle-orm`, `drizzle-kit`): Type-safe schema definition and migrations
- **Drizzle-Zod** (`drizzle-zod`): Schema validation integration

### UI Component Libraries
- **Radix UI**: Headless component primitives (dialogs, dropdowns, tabs, tooltips, etc.)
- **Recharts**: Charting library for data visualization
- **Embla Carousel**: Carousel/slider functionality
- **Lucide React**: Icon library
- **CMDK**: Command palette component

### Styling & Theming
- **Tailwind CSS**: Utility-first CSS framework
- **Class Variance Authority**: Type-safe component variant handling
- **clsx/tailwind-merge**: Conditional class composition

### Forms & Validation
- **React Hook Form**: Form state management
- **Zod**: Schema validation
- **@hookform/resolvers**: Validation resolver bridge

### Development Tools
- **Vite**: Build tool and dev server
- **esbuild**: Production bundling for server code
- **tsx**: TypeScript execution for development
- **Replit Plugins**: Runtime error overlay, cartographer, dev banner for Replit environment

### Utilities
- **date-fns**: Date manipulation and formatting
- **nanoid**: Unique ID generation
- **wouter**: Lightweight routing library

### Session Management
- **Express Session**: Session middleware
- **connect-pg-simple**: PostgreSQL session store

### Type Safety
- TypeScript configured for strict mode across client, server, and shared code
- Path resolution for monorepo structure
- ESM module system throughout