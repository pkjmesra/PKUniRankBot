"""
Unit tests for University Ranking System
"""

import unittest
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ranking.pkUniRankBot import EnhancedUniversityRankingSystem, UniversityRankingSystem
import numpy as np

class TestUniversityRankingSystem(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures"""
        self.ranking_system = EnhancedUniversityRankingSystem()
        self.basic_system = UniversityRankingSystem()
        
    def test_known_university_scores(self):
        """Test that known universities return their pre-defined scores"""
        print("\n=== Test 1: Known University Scores ===")
        
        test_cases = [
            ("Harvard University", "USA", 25 + 24 + 20 + 13 + 10 + 5),  # Should be 97
            ("Stanford University", "USA", 24 + 23 + 21 + 14 + 9 + 5),   # Should be 96
            ("MIT", "USA", 24 + 23 + 22 + 14 + 9 + 5),                   # Should be 97
            ("University of Toronto", "Canada", 22 + 21 + 18 + 13 + 9 + 4),  # Should be 87
            ("Bryant University", "USA", 12 + 22 + 16 + 13 + 8 + 3),     # Should be 74
            ("North Dakota State University", "USA", 15.6 + 15.0 + 16.1 + 11.0 + 9.0 + 4.0),  # Should be ~70.7
        ]
        
        for uni_name, country, expected_score in test_cases:
            print(f"\nTesting {uni_name} ({country})...")
            
            # Get scores from the basic system (which uses database)
            name_lower = uni_name.lower()
            if name_lower in self.basic_system.university_db:
                scores = self.basic_system.university_db[name_lower]['scores']
                composite = sum(scores.values())
                
                print(f"  Database scores: {scores}")
                print(f"  Composite: {composite:.1f}, Expected: {expected_score:.1f}")
                
                # Check they're not all the same
                self.assertNotEqual(composite, 62.0, 
                    f"{uni_name} should not have score 62.0, got {composite}")
                
                # Check it's close to expected
                self.assertAlmostEqual(composite, expected_score, delta=0.5,
                    msg=f"{uni_name} score mismatch: got {composite}, expected {expected_score}")
                
                print(f"  ✓ Score: {composite:.1f}")
            else:
                print(f"  ⚠️ {uni_name} not in database")
    
    def test_estimate_scores_variation(self):
        """Test that estimated scores vary for different universities"""
        print("\n=== Test 2: Estimated Scores Variation ===")
        
        universities = [
            ("Small Liberal Arts College", "USA"),
            ("State Technical University", "Germany"),
            ("Community College", "Canada"),
            ("Research Institute of Technology", "USA"),
            ("Business School", "UK"),
        ]
        
        scores = []
        for uni_name, country in universities:
            print(f"\nEstimating scores for {uni_name} ({country})...")
            
            # Get estimated scores
            estimated_scores = self.basic_system.estimate_scores(uni_name, country)
            composite = sum(estimated_scores.values())
            
            print(f"  Estimated scores: {estimated_scores}")
            print(f"  Composite: {composite:.1f}")
            
            scores.append(composite)
        
        # Check that scores are not all the same
        unique_scores = set(round(s, 1) for s in scores)
        print(f"\nUnique scores: {unique_scores}")
        
        self.assertGreater(len(unique_scores), 1,
            f"All estimated scores are the same: {scores}")
        
        print("  ✓ Scores vary across different universities")
    
    def test_country_multiplier_effect(self):
        """Test that country multipliers affect scores"""
        print("\n=== Test 3: Country Multiplier Effect ===")
        
        same_university = "Generic University"
        countries = ["USA", "India", "Germany", "Brazil"]
        
        scores_by_country = {}
        for country in countries:
            print(f"\nEstimating for {same_university} in {country}...")
            
            estimated_scores = self.basic_system.estimate_scores(same_university, country)
            composite = sum(estimated_scores.values())
            
            scores_by_country[country] = composite
            print(f"  Composite: {composite:.1f}")
        
        # Check that scores differ by country
        unique_scores = set(round(s, 1) for s in scores_by_country.values())
        print(f"\nScores by country: {scores_by_country}")
        print(f"Unique scores: {unique_scores}")
        
        self.assertGreater(len(unique_scores), 1,
            f"Scores should vary by country, but got: {scores_by_country}")
        
        print("  ✓ Scores vary by country")
    
    def test_university_type_classification(self):
        """Test that university type classification works correctly"""
        print("\n=== Test 4: University Type Classification ===")
        
        test_cases = [
            ("Harvard University", "RESEARCH_UNIVERSITY"),
            ("MIT", "RESEARCH_UNIVERSITY"),
            ("Stanford University", "RESEARCH_UNIVERSITY"),
            ("Conestoga College", "COLLEGE_POLYTECHNIC"),
            ("Algonquin College", "COLLEGE_POLYTECHNIC"),
            ("Harvard Business School", "SPECIALIST_SCHOOL"),
            ("State Technical Institute", "APPLIED_UNIVERSITY"),
            ("Generic University", "TEACHING_UNIVERSITY"),
        ]
        
        for uni_name, expected_type in test_cases:
            uni_type = self.basic_system.classify_university_type(uni_name)
            print(f"{uni_name}: {uni_type} (expected: {expected_type})")
            
            self.assertEqual(uni_type, expected_type,
                f"Type mismatch for {uni_name}: got {uni_type}, expected {expected_type}")
        
        print("  ✓ All types classified correctly")
    
    def test_composite_score_calculation(self):
        """Test composite score calculation"""
        print("\n=== Test 5: Composite Score Calculation ===")
        
        # Create test scores
        test_scores = {
            'academic': 20.0,
            'graduate': 18.0,
            'roi': 16.0,
            'fsr': 12.0,
            'transparency': 8.0,
            'visibility': 4.0
        }
        
        expected_composite = 20 + 18 + 16 + 12 + 8 + 4  # = 78
        composite = self.basic_system.calculate_composite_score(test_scores)
        
        print(f"Test scores: {test_scores}")
        print(f"Calculated composite: {composite}, Expected: {expected_composite}")
        
        self.assertEqual(composite, expected_composite,
            f"Composite calculation wrong: got {composite}, expected {expected_composite}")
        
        print("  ✓ Composite calculation correct")
    
    def test_tier_assignment(self):
        """Test that tiers are assigned correctly based on scores"""
        print("\n=== Test 6: Tier Assignment ===")
        
        test_cases = [
            (95.0, "A+", "🎖️ WORLD-CLASS"),
            (80.0, "A", "⭐ EXCELLENT"),
            (70.0, "B", "👍 GOOD"),
            (60.0, "C+", "📊 AVERAGE"),
            (50.0, "C", "⚠️ BELOW AVERAGE"),
            (30.0, "D", "🚨 POOR"),
        ]
        
        for score, expected_tier, expected_desc in test_cases:
            tier, desc = self.basic_system.get_tier(score)
            print(f"Score {score}: Tier {tier} ({desc})")
            
            self.assertEqual(tier, expected_tier,
                f"Tier mismatch for score {score}: got {tier}, expected {expected_tier}")
            self.assertEqual(desc, expected_desc,
                f"Description mismatch for score {score}")
        
        print("  ✓ All tiers assigned correctly")
    
    def test_enhanced_ranking_system(self):
        """Test the enhanced ranking system with real data fetching"""
        print("\n=== Test 7: Enhanced Ranking System ===")
        
        # Test with a known university
        uni_name = "Harvard University"
        country = "USA"
        
        print(f"\nTesting enhanced ranking for {uni_name}...")
        
        # Mock user config
        class MockUserConfig:
            enable_wikipedia = True
            enable_google_search = False
            enable_webometrics = False
        
        user_config = MockUserConfig()
        
        # Get ranking
        ranking_data = self.ranking_system.rank_university(
            uni_name, country, "test_user", user_config
        )
        
        print(f"  Name: {ranking_data.name}")
        print(f"  Country: {ranking_data.country}")
        print(f"  Type: {ranking_data.type}")
        print(f"  Scores: {ranking_data.scores}")
        print(f"  Composite: {ranking_data.composite}")
        print(f"  Tier: {ranking_data.tier}")
        print(f"  Error Margin: {ranking_data.error_margin}")
        print(f"  Is Estimated: {ranking_data.is_estimated}")
        
        # Basic validation
        self.assertIsNotNone(ranking_data)
        self.assertEqual(ranking_data.name, uni_name)
        self.assertEqual(ranking_data.country, country)
        self.assertIsInstance(ranking_data.composite, float)
        self.assertGreater(ranking_data.composite, 0)
        self.assertLessEqual(ranking_data.composite, 100)
        
        print("  ✓ Enhanced ranking works")
    
    def test_score_distribution(self):
        """Test that scores are distributed across a reasonable range"""
        print("\n=== Test 8: Score Distribution Test ===")
        
        universities = [
            ("Top Research University", "USA"),
            ("Mid-tier State University", "USA"),
            ("Small Teaching College", "Canada"),
            ("Technical Institute", "Germany"),
            ("Community College", "Australia"),
        ]
        
        composites = []
        for uni_name, country in universities:
            estimated_scores = self.basic_system.estimate_scores(uni_name, country)
            composite = sum(estimated_scores.values())
            composites.append(composite)
            
            print(f"{uni_name}: {composite:.1f}")
        
        # Calculate statistics
        avg_score = np.mean(composites)
        min_score = min(composites)
        max_score = max(composites)
        score_range = max_score - min_score
        
        print(f"\nStatistics:")
        print(f"  Average: {avg_score:.1f}")
        print(f"  Range: {min_score:.1f} - {max_score:.1f} (span: {score_range:.1f})")
        print(f"  All scores: {[round(s, 1) for s in composites]}")
        
        # Assertions
        self.assertGreater(score_range, 5, 
            f"Scores should vary more than 5 points, but range is only {score_range}")
        self.assertNotAlmostEqual(avg_score, 62.0, delta=10,msg=f"Average score should not be around 62, got {avg_score}")
        
        print("  ✓ Scores have reasonable distribution")
    
    def test_parameter_weights(self):
        """Test that parameter weights are applied correctly"""
        print("\n=== Test 9: Parameter Weights Test ===")
        
        # Check parameter max scores
        parameters = self.basic_system.parameters
        
        print("Parameter weights:")
        total_max = 0
        for param_code, param_info in parameters.items():
            max_score = param_info['max']
            total_max += max_score
            print(f"  {param_info['name']}: {max_score}")
        
        print(f"Total maximum: {total_max}")
        
        self.assertEqual(total_max, 100, 
            f"Total maximum score should be 100, got {total_max}")
        
        # Check individual weights are reasonable
        self.assertEqual(parameters['academic']['max'], 25)
        self.assertEqual(parameters['graduate']['max'], 25)
        self.assertEqual(parameters['roi']['max'], 20)
        self.assertEqual(parameters['fsr']['max'], 15)
        self.assertEqual(parameters['transparency']['max'], 10)
        self.assertEqual(parameters['visibility']['max'], 5)
        
        print("  ✓ Parameter weights are correct")

def run_tests():
    """Run all tests"""
    print("=" * 60)
    print("RUNNING UNIVERSITY RANKING SYSTEM TESTS")
    print("=" * 60)
    
    # Create test suite
    suite = unittest.TestLoader().loadTestsFromTestCase(TestUniversityRankingSystem)
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    if result.wasSuccessful():
        print("✅ All tests passed!")
    else:
        print(f"❌ Tests failed: {len(result.failures)} failures, {len(result.errors)} errors")
        
        # Print details of failures
        for test, traceback in result.failures:
            print(f"\nFAILED: {test}")
            print(traceback)
        
        for test, traceback in result.errors:
            print(f"\nERROR: {test}")
            print(traceback)
    
    return result.wasSuccessful()

# if __name__ == "__main__":
#     success = run_tests()
#     sys.exit(0 if success else 1)
