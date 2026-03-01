using System;
using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using System.Linq;

namespace Vidly.Models
{
    /// <summary>
    /// Discount calculation method.
    /// </summary>
    public enum DiscountType
    {
        /// <summary>Percentage off the rental cost (e.g. 25% off).</summary>
        [Display(Name = "Percentage")]
        Percentage = 1,

        /// <summary>Fixed dollar amount off (e.g. $2.00 off).</summary>
        [Display(Name = "Fixed Amount")]
        FixedAmount = 2,

        /// <summary>Override the daily rate (e.g. $1.99/day instead of $3.99).</summary>
        [Display(Name = "Daily Rate Override")]
        DailyRateOverride = 3,

        /// <summary>Free rental (100% off).</summary>
        [Display(Name = "Free Rental")]
        FreeRental = 4
    }

    /// <summary>
    /// Defines a promotional discount with eligibility criteria.
    /// </summary>
    public class Promotion
    {
        public int Id { get; set; }

        [Required(ErrorMessage = "Promotion name is required.")]
        [StringLength(200, ErrorMessage = "Promotion name cannot exceed 200 characters.")]
        public string Name { get; set; }

        [StringLength(500, ErrorMessage = "Description cannot exceed 500 characters.")]
        public string Description { get; set; }

        /// <summary>Promotional offer code (optional, case-insensitive).</summary>
        [StringLength(50, ErrorMessage = "Promo code cannot exceed 50 characters.")]
        public string PromoCode { get; set; }

        [Required]
        public DiscountType DiscountType { get; set; }

        /// <summary>
        /// Discount value: percentage (0-100), fixed dollar amount, or daily rate override.
        /// Ignored for FreeRental type.
        /// </summary>
        [Range(0, 9999.99, ErrorMessage = "Discount value must be non-negative.")]
        public decimal DiscountValue { get; set; }

        /// <summary>Start date of the promotion (inclusive). Null means no start restriction.</summary>
        [DataType(DataType.Date)]
        public DateTime? StartDate { get; set; }

        /// <summary>End date of the promotion (inclusive). Null means no end restriction.</summary>
        [DataType(DataType.Date)]
        public DateTime? EndDate { get; set; }

        /// <summary>If set, only applies to this genre.</summary>
        public Genre? RequiredGenre { get; set; }

        /// <summary>If set, only applies to customers with this membership tier or higher.</summary>
        public MembershipType? MinimumMembership { get; set; }

        /// <summary>
        /// If set, customer must have at least this many completed (returned) rentals.
        /// Useful for loyalty rewards.
        /// </summary>
        [Range(0, int.MaxValue)]
        public int? MinimumRentalCount { get; set; }

        /// <summary>Whether this promotion is currently enabled.</summary>
        public bool IsActive { get; set; } = true;

        /// <summary>Maximum number of times this promotion can be used total. Null = unlimited.</summary>
        [Range(1, int.MaxValue)]
        public int? MaxUses { get; set; }

        /// <summary>Number of times this promotion has been redeemed.</summary>
        public int TimesUsed { get; set; }

        /// <summary>Maximum times a single customer can use this promotion. Null = unlimited.</summary>
        [Range(1, int.MaxValue)]
        public int? MaxUsesPerCustomer { get; set; }

        /// <summary>Priority for stacking resolution — higher priority wins when promotions conflict.</summary>
        [Range(0, 100)]
        public int Priority { get; set; }

        /// <summary>Whether this promotion can combine with other promotions on the same rental.</summary>
        public bool Stackable { get; set; }

        /// <summary>
        /// Checks whether the promotion is within its valid date range on the given date.
        /// </summary>
        public bool IsWithinDateRange(DateTime date)
        {
            if (StartDate.HasValue && date.Date < StartDate.Value.Date)
                return false;
            if (EndDate.HasValue && date.Date > EndDate.Value.Date)
                return false;
            return true;
        }

        /// <summary>
        /// Whether this promotion has remaining uses (not exhausted).
        /// </summary>
        public bool HasRemainingUses => !MaxUses.HasValue || TimesUsed < MaxUses.Value;

        /// <summary>
        /// Quick check: active, within date range on today, and has remaining uses.
        /// Does NOT check customer-specific criteria (membership, rental count).
        /// </summary>
        public bool IsCurrentlyValid => IsActive && IsWithinDateRange(DateTime.Today) && HasRemainingUses;
    }

    /// <summary>
    /// Result of applying a promotion to a rental.
    /// </summary>
    public class PromotionResult
    {
        /// <summary>The promotion that was applied.</summary>
        public Promotion Promotion { get; set; }

        /// <summary>Original rental cost before discount.</summary>
        public decimal OriginalCost { get; set; }

        /// <summary>Discount amount removed.</summary>
        public decimal DiscountAmount { get; set; }

        /// <summary>Final cost after discount (never negative).</summary>
        public decimal FinalCost { get; set; }

        /// <summary>Effective daily rate after promotion.</summary>
        public decimal EffectiveDailyRate { get; set; }

        /// <summary>Savings as a percentage of original cost.</summary>
        public decimal SavingsPercent =>
            OriginalCost > 0 ? Math.Round(DiscountAmount / OriginalCost * 100, 2) : 0;

        /// <summary>Human-readable description of the discount applied.</summary>
        public string Summary { get; set; }
    }

    /// <summary>
    /// Result of evaluating multiple promotions (potentially stacked).
    /// </summary>
    public class PromotionEvaluation
    {
        /// <summary>All individual promotion results applied.</summary>
        public List<PromotionResult> AppliedPromotions { get; set; } = new List<PromotionResult>();

        /// <summary>Promotions that were eligible but not applied (lower priority or non-stackable conflict).</summary>
        public List<Promotion> SkippedPromotions { get; set; } = new List<Promotion>();

        /// <summary>Original cost before any discounts.</summary>
        public decimal OriginalCost { get; set; }

        /// <summary>Total discount from all applied promotions.</summary>
        public decimal TotalDiscount { get; set; }

        /// <summary>Final cost after all discounts.</summary>
        public decimal FinalCost { get; set; }

        /// <summary>Total savings percentage.</summary>
        public decimal TotalSavingsPercent =>
            OriginalCost > 0 ? Math.Round(TotalDiscount / OriginalCost * 100, 2) : 0;

        /// <summary>Whether any promotions were applied.</summary>
        public bool HasDiscounts => AppliedPromotions.Count > 0;

        /// <summary>Number of promotions applied.</summary>
        public int PromotionCount => AppliedPromotions.Count;

        /// <summary>Promo code(s) used, comma-separated.</summary>
        public string AppliedCodes
        {
            get
            {
                var codes = AppliedPromotions
                    .Where(p => !string.IsNullOrWhiteSpace(p.Promotion.PromoCode))
                    .Select(p => p.Promotion.PromoCode)
                    .ToList();
                return codes.Count > 0 ? string.Join(", ", codes) : null;
            }
        }

        /// <summary>Human-readable summary of all applied promotions.</summary>
        public string Summary
        {
            get
            {
                if (!HasDiscounts)
                    return "No promotions applied.";

                var parts = new List<string>();
                foreach (var pr in AppliedPromotions)
                    parts.Add(pr.Summary);

                return string.Join(" + ", parts) +
                       $" | Total savings: ${TotalDiscount:F2} ({TotalSavingsPercent}% off)";
            }
        }
    }
}
