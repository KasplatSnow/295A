#!/bin/bash

# VigilZone UI Setup Script for Linux
# This script sets up the UI portion of the project (Frontend + Backend)
# AI/Python components are NOT included

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Functions
print_header() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Check if running in correct directory
check_directory() {
    if [ ! -f "package.json" ] || [ ! -d "client" ] || [ ! -d "server" ]; then
        print_error "Please run this script from the UI_ver_1_2 directory"
        exit 1
    fi
}

# Check if Node.js is installed
check_node() {
    print_info "Checking Node.js installation..."
    if ! command -v node &> /dev/null; then
        print_error "Node.js is not installed"
        print_info "Please install Node.js 18+ from: https://nodejs.org/"
        print_info "Or use: sudo apt install nodejs npm (Ubuntu/Debian)"
        exit 1
    fi
    
    NODE_VERSION=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
    if [ "$NODE_VERSION" -lt 18 ]; then
        print_warning "Node.js version is $NODE_VERSION. Recommended version is 18+"
        read -p "Continue anyway? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    else
        print_success "Node.js $(node -v) detected"
    fi
}

# Check if npm is installed
check_npm() {
    print_info "Checking npm installation..."
    if ! command -v npm &> /dev/null; then
        print_error "npm is not installed"
        print_info "Please install npm"
        exit 1
    fi
    print_success "npm $(npm -v) detected"
}

# Clean previous installations
clean_install() {
    print_info "Cleaning previous installations..."
    if [ -d "node_modules" ]; then
        print_warning "Removing existing node_modules..."
        rm -rf node_modules
        print_success "node_modules removed"
    fi
    
    if [ -f "package-lock.json" ]; then
        print_warning "Removing existing package-lock.json..."
        rm -f package-lock.json
        print_success "package-lock.json removed"
    fi
}

# Install Node dependencies
install_dependencies() {
    print_info "Installing Node.js dependencies..."
    print_warning "This may take a few minutes..."
    
    if npm install; then
        print_success "Dependencies installed successfully"
    else
        print_error "Failed to install dependencies"
        print_info "Try running: npm install --legacy-peer-deps"
        exit 1
    fi
}

# Check if .env file exists
check_env() {
    print_info "Checking environment configuration..."
    if [ ! -f ".env" ]; then
        print_warning ".env file not found"
        print_info "Creating default .env file..."
        
        cat > .env << EOF
# Database (optional - can run without it)
# DATABASE_URL=postgresql://user:pass@host/db

# Server
NODE_ENV=development
PORT=5000

# Session (optional)
# SESSION_SECRET=your-secret-key-here
EOF
        print_success ".env file created"
    else
        print_success ".env file exists"
    fi
}

# Verify installation
verify_installation() {
    print_info "Verifying installation..."
    
    # Check if node_modules exists
    if [ ! -d "node_modules" ]; then
        print_error "node_modules directory not found"
        return 1
    fi
    
    # Check if key packages are installed
    if [ ! -d "node_modules/react" ]; then
        print_error "React not found in node_modules"
        return 1
    fi
    
    if [ ! -d "node_modules/vite" ]; then
        print_error "Vite not found in node_modules"
        return 1
    fi
    
    print_success "Installation verified"
    return 0
}

# Display post-install instructions
show_instructions() {
    print_header "Setup Complete!"
    
    echo -e "${GREEN}The UI project is ready to run!${NC}"
    echo ""
    echo -e "${CYAN}Available Commands:${NC}"
    echo ""
    echo -e "  ${YELLOW}npm run dev${NC}     - Start development server"
    echo -e "  ${YELLOW}npm run build${NC}   - Build for production"
    echo -e "  ${YELLOW}npm start${NC}       - Start production server"
    echo -e "  ${YELLOW}npm run check${NC}   - Type checking"
    echo ""
    echo -e "${CYAN}Access Points:${NC}"
    echo ""
    echo -e "  Frontend: ${BLUE}http://localhost:5000${NC}"
    echo -e "  Backend API: ${BLUE}http://localhost:5000/api${NC}"
    echo ""
    echo -e "${CYAN}Project Structure:${NC}"
    echo ""
    echo "  client/     - React frontend (TypeScript + Vite)"
    echo "  server/     - Express backend (Node.js + TypeScript)"
    echo "  shared/     - Shared types and schemas"
    echo ""
}

# Main installation process
main() {
    print_header "VigilZone UI Setup (Linux)"
    
    # Check prerequisites
    check_directory
    check_node
    check_npm
    
    echo ""
    print_warning "This script will:"
    echo "  1. Clean previous installations (if any)"
    echo "  2. Install all Node.js dependencies"
    echo "  3. Create .env file (if missing)"
    echo "  4. Verify installation"
    echo ""
    
    read -p "Continue with setup? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_warning "Setup cancelled"
        exit 0
    fi
    
    echo ""
    
    # Run installation steps
    clean_install
    echo ""
    
    install_dependencies
    echo ""
    
    check_env
    echo ""
    
    if verify_installation; then
        echo ""
        show_instructions
        
        # Ask if user wants to start dev server
        echo ""
        read -p "Would you like to start the development server now? (y/n) " -n 1 -r
        echo
        echo ""
        
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            print_info "Starting development server..."
            print_warning "Press Ctrl+C to stop the server"
            echo ""
            sleep 2
            npm run dev
        else
            print_info "To start the server later, run: ${YELLOW}npm run dev${NC}"
            echo ""
        fi
    else
        print_error "Installation verification failed"
        print_info "Please check the errors above and try again"
        exit 1
    fi
}

# Run main function
main
