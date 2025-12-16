#!/usr/bin/env python3
"""
Mock Weather Report Generator
Prints colorful weather reports with emoji for any city
"""

import random
import sys
from datetime import datetime, timedelta


# ANSI color codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    WHITE = "\033[97m"


# Weather conditions with emoji
WEATHER_CONDITIONS = [
    ("Sunny", "☀️", Colors.YELLOW),
    ("Partly Cloudy", "⛅", Colors.CYAN),
    ("Cloudy", "☁️", Colors.WHITE),
    ("Rainy", "🌧️", Colors.BLUE),
    ("Stormy", "⛈️", Colors.MAGENTA),
    ("Snowy", "❄️", Colors.CYAN),
    ("Foggy", "🌫️", Colors.WHITE),
    ("Windy", "💨", Colors.CYAN),
]


def get_mock_weather():
    """Generate mock weather data"""
    condition, emoji, color = random.choice(WEATHER_CONDITIONS)
    temp = random.randint(-10, 35)
    humidity = random.randint(30, 90)
    wind_speed = random.randint(5, 40)

    return {
        "condition": condition,
        "emoji": emoji,
        "color": color,
        "temp": temp,
        "humidity": humidity,
        "wind_speed": wind_speed,
    }


def print_header(city):
    """Print colorful header"""
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 50}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.GREEN}🌍 Weather Report for {city.title()} 🌍{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 50}{Colors.RESET}\n")


def print_current_weather(weather):
    """Print current weather conditions"""
    print(f"{Colors.BOLD}{Colors.YELLOW}Current Conditions:{Colors.RESET}")
    print(f"{weather['color']}{weather['emoji']}  {weather['condition']}{Colors.RESET}")
    print(f"{Colors.RED}🌡️  Temperature: {weather['temp']}°C{Colors.RESET}")
    print(f"{Colors.BLUE}💧 Humidity: {weather['humidity']}%{Colors.RESET}")
    print(f"{Colors.CYAN}💨 Wind Speed: {weather['wind_speed']} km/h{Colors.RESET}")
    print()


def print_forecast():
    """Print 3-day forecast"""
    print(f"{Colors.BOLD}{Colors.MAGENTA}📅 3-Day Forecast:{Colors.RESET}\n")

    today = datetime.now()

    for i in range(1, 4):
        forecast_date = today + timedelta(days=i)
        weather = get_mock_weather()
        day_name = forecast_date.strftime("%A")

        print(
            f"{Colors.BOLD}{Colors.GREEN}{day_name}, {forecast_date.strftime('%B %d')}:{Colors.RESET}"
        )
        print(f"  {weather['color']}{weather['emoji']}  {weather['condition']}{Colors.RESET}")
        print(
            f"  {Colors.RED}High: {weather['temp']}°C{Colors.RESET} / {Colors.BLUE}Low: {weather['temp'] - random.randint(5, 10)}°C{Colors.RESET}"
        )
        print()


def main():
    """Main function to generate and display weather report"""
    if len(sys.argv) < 2:
        print(f"{Colors.RED}❌ Error: Please provide a city name{Colors.RESET}")
        print(f"{Colors.YELLOW}Usage: python weather.py <city_name>{Colors.RESET}")
        sys.exit(1)

    city = " ".join(sys.argv[1:])

    print_header(city)

    current_weather = get_mock_weather()
    print_current_weather(current_weather)

    print_forecast()

    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 50}{Colors.RESET}")
    print(f"{Colors.GREEN}✨ Have a great day! ✨{Colors.RESET}\n")


if __name__ == "__main__":
    main()
