package service

import "errors"

type Order struct {
	ID     string
	Amount float64
}

type OrderService struct{}

func (s *OrderService) ProcessOrder(order *Order) error {
	if order == nil {
		return errors.New("nil order")
	}
	if order.Amount <= 0 {
		return errors.New("invalid amount")
	}
	return nil
}
